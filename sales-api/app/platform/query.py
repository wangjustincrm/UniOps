r"""Query specification for list endpoints (design §13.3; the list query
contract, §13.19).

The UniOps list contract is `{items, total}`. Sort and filter keys are names a
resource declares; the repository maps them to columns, so nothing a client
sends reaches SQL as an identifier.

    ?filter=field:op:value   repeated; different filters are AND
    value                    v1|v2 — several values; `\|` is a literal |, `\\` a backslash
                             positive operators: any value (OR);
                             ne / not_contains / not_in: none of them
    between                  low..high, either side empty (`range` = alias)
    is_null / not_null       no value (`field:is_null:`)
    sort=a,-b   search=…   page=…   page_size=…
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

from app.platform.errors import InvalidInput

T = TypeVar("T")

FilterOp = Literal["eq", "ne", "contains", "not_contains", "starts_with", "ends_with", "in", "not_in",
                   "gt", "gte", "lt", "lte", "between", "range", "is_null", "not_null"]
FieldKind = Literal["text", "number", "date", "datetime", "enum", "bool"]

# The operators a field allows, by its kind (the client builds its menus from
# /meta, which lists them per field). `range` is the old name of `between`.
TEXT_OPS = ("contains", "not_contains", "starts_with", "ends_with", "eq", "ne", "in", "not_in", "is_null", "not_null")
ORDERED_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "not_in", "is_null", "not_null")
CHOICE_OPS = ("eq", "ne", "in", "not_in", "is_null", "not_null")
BOOL_OPS = ("eq", "ne", "is_null", "not_null")
OPS_BY_KIND: dict[str, tuple[str, ...]] = {
    "text": TEXT_OPS, "number": ORDERED_OPS, "date": ORDERED_OPS, "datetime": ORDERED_OPS,
    "enum": CHOICE_OPS, "bool": BOOL_OPS,
}
ALL_OPS = frozenset(FilterOp.__args__)  # type: ignore[attr-defined]
NO_VALUE_OPS = frozenset({"is_null", "not_null"})
RANGE_OPS = frozenset({"between", "range"})
SINGLE_VALUE_OPS = frozenset({"gt", "gte", "lt", "lte"})
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
    # A value or a list of values (OR; none of them for the negative
    # operators); between / range: [low, high] with None for open; nothing
    # for is_null / not_null.
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
    is a 422 here and never a database error. `ops` left out = every operator
    of the field's kind (OPS_BY_KIND); `kind` left out = from the column."""

    column: Any
    ops: frozenset[str] | None = None
    cast: Callable[[Any], Any] | None = None
    kind: FieldKind | None = None

    def convert(self, value: Any) -> Any:
        return (self.cast or cast_for(self.column))(value)


def kind_for(column: Any) -> FieldKind:
    """A column's field kind from its Python type (text when unknown)."""
    try:
        py = column.type.python_type
    except (AttributeError, NotImplementedError):
        return "text"
    if py is bool:
        return "bool"
    if py is datetime:
        return "datetime"
    if py is date:
        return "date"
    if py in (int, float, Decimal):
        return "number"
    return "text"


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
    if py is datetime:  # a time without a zone is UTC (timestamptz never compares with a naive value)
        def to_datetime(v: Any) -> datetime:
            d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return to_datetime
    if py is date:
        return lambda v: v if isinstance(v, date) else date.fromisoformat(str(v))
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


def split_values(value: str) -> list[str]:
    """`a|b\\|c` → ["a", "b|c"]: `|` separates, `\\|` and `\\\\` escape."""
    out, cur, i = [], "", 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and value[i + 1] in "|\\":
            cur += value[i + 1]
            i += 2
            continue
        if ch == "|":
            out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    return [v for v in [*out, cur] if v != ""]


def parse_filter(raw: str) -> Filter:
    """`field:op:value` (module docstring)."""
    field, sep, rest = raw.partition(":")
    op, sep2, value = rest.partition(":")
    if not sep or not sep2 or not field:
        raise InvalidInput("query.bad_filter", f"filter {raw!r} is not field:op:value")
    if op not in ALL_OPS:
        raise InvalidInput("query.unsupported_operator", f"unknown filter operator {op!r}", {"op": op})
    if op in NO_VALUE_OPS:
        return Filter(field=field, op=op, value=None)
    if op in RANGE_OPS:
        if len(value) > MAX_VALUE * 2 + 2:
            raise InvalidInput("query.value_too_long", f"a filter value is longer than {MAX_VALUE}", {"field": field})
        low, dots, high = value.partition("..")
        if not dots:
            raise InvalidInput("query.bad_filter", f"{op} filter {raw!r} needs low..high")
        return Filter(field=field, op=op, value=[low or None, high or None])
    values = split_values(value)
    if not values:
        raise InvalidInput("query.bad_filter", f"filter {raw!r} has no value", {"field": field})
    if len(values) > MAX_IN_VALUES:
        raise InvalidInput("query.too_many_values", f"at most {MAX_IN_VALUES} values in one filter",
                           {"field": field, "max": MAX_IN_VALUES})
    if any(len(v) > MAX_VALUE for v in values):
        raise InvalidInput("query.value_too_long", f"a filter value is longer than {MAX_VALUE}", {"field": field})
    if op in SINGLE_VALUE_OPS and len(values) > 1:
        raise InvalidInput("query.bad_filter", f"{op} takes one value", {"field": field})
    return Filter(field=field, op=op, value=values if len(values) > 1 or op in ("in", "not_in") else values[0])


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
