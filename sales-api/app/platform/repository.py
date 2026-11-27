"""Repository (design §13.3).

    class PartnerRepository(Repository[Partner]):
        model = Partner
        search_fields = ("code", "name")
        list_fields = ("code", "name", "channel", "active")   # sortable + filterable columns
        sortable = {...}; filterable = {...}                   # extra or special keys, if any
        labels = {"code": "Partner code"}                      # else humanised from the name
        enums = {"channel": CHANNELS}                          # an enum field and its values
        groupable = ("channel", "active")                      # fields rows can be grouped by
        summable = ("total",); sum_currency = "currency"       # list totals, per currency
        money_fields = ("total",)                              # amounts: shown with the row's currency

The same declaration drives the list query and `/meta` (field_meta), from
which the client builds its column menus and search panel, and offers every
field the rows carry (`in_row`) as a column.

There is deliberately no `delete`: config rows are deactivated, documents are
never removed. Models that opt in to SoftDeletable get `soft_delete`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import UnionType
from typing import Any, ClassVar, Generic, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import Select, and_, func, not_, or_, select, true

from app.platform.record_types import Ref
from app.platform.clock import clock
from app.platform.relative_dates import is_relative, period
from app.platform.entities import SoftDeletable
from app.platform.errors import InvalidInput, NotFound
from app.platform.views import Preset
from app.platform.query import (  # noqa: F401 — FilterRule, parse_bool re-exported
    MAX_GROUPS, NO_VALUE_OPS, OPS_BY_KIND, RANGE_OPS, Filter, FilterRule, Group, Page, QuerySpec, Total, kind_for,
    parse_bool, parse_sort,
)

T = TypeVar("T")
MAX_SUGGESTIONS = 20
OTHER = "Other"


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Repository(Generic[T]):
    model: ClassVar[type]
    search_fields: ClassVar[tuple[str, ...]] = ()
    # Columns only the page has (computed in the client, e.g. a Detail or a Ship to cell): the system
    # views may name them beside the fields. Anything else a view names is a typo (2026-11-10).
    client_columns: ClassVar[tuple[str, ...]] = ()
    # Integer columns a search of digits only matches exactly (an internal number).
    search_numbers: ClassVar[tuple[str, ...]] = ()
    sortable: ClassVar[Mapping[str, Any]] = {}
    filterable: ClassVar[Mapping[str, FilterRule]] = {}
    # Model columns that are both sortable and filterable (merged into the two
    # maps above when the class is defined; an explicit entry there wins).
    list_fields: ClassVar[tuple[str, ...]] = ()
    # In the read schema but never offered to a client as a column or a field (/meta).
    meta_hidden: ClassVar[tuple[str, ...]] = ()
    labels: ClassVar[Mapping[str, str]] = {}
    # The search panel's groups (#32, user 2026-11-26): (label, fields), one level, in order. Declared, every field /meta
    # offers for a filter is in exactly one group — "Other" named on purpose (tests/test_field_groups.py). Not declared:
    # every field is in "Other".
    field_groups: ClassVar[tuple[tuple[str, tuple[str, ...]], ...]] = ()
    # Fields that name a record with a page of its own (platform/record_types.py): sent in /meta as `ref`, so the client
    # links them. Declared, never guessed from a name (tests/test_record_refs.py makes sure none is forgotten).
    refs: ClassVar[Mapping[str, Ref]] = {}
    # Record types the record's own detail names beyond its list fields (an order's lines name items, its packing lots):
    # /meta says whether this person may read them, so the detail links them by the same one key.
    detail_refs: ClassVar[tuple[str, ...]] = ()
    enums: ClassVar[Mapping[str, Sequence[str]]] = {}
    # Fields the list can be grouped by (sortable or filterable keys): the
    # values that repeat — a status, a partner, a flag — not free text.
    groupable: ClassVar[tuple[str, ...]] = ()
    # Filterable text fields whose existing values are offered while typing (/suggest). Opt in, field by
    # field: never a person's name, email, phone or address, or anyone could read them out letter by letter.
    suggestable: ClassVar[tuple[str, ...]] = ()
    # Numeric fields (sortable keys) summed over the whole filtered result —
    # the list footer and the group headers — per value of `sum_currency`.
    summable: ClassVar[tuple[str, ...]] = ()
    sum_currency: ClassVar[str | None] = None
    # Fields that are amounts of money, shown with the currency in `money_currency` (else
    # `sum_currency`) of the same row. Declared, never guessed from the column's type: a
    # weight, a quantity or a factor can have two decimals too.
    money_fields: ClassVar[tuple[str, ...]] = ()
    money_currency: ClassVar[str | None] = None
    # Amounts are totalled too (footer and group headers, per currency), like `summable` — except these:
    # a value repeated on each row (e.g. an order's total on each of its payments) would add up wrong.
    unsummed: ClassVar[tuple[str, ...]] = ()
    totalled: ClassVar[tuple[str, ...]] = ()  # set at class definition: summable + the amounts
    # System views (design §13.19): named partial view states, in /meta.
    presets: ClassVar[tuple[Preset, ...]] = ()
    default_sort: ClassVar[tuple[str, ...]] = ()  # sortable keys, "-" for descending
    # A boolean column marking rows imported as history (e.g. from the system
    # being replaced). Lists show them like any other row (user 2026-11-12: the
    # difference lives on the record, not in the tables); only open-work lists
    # (`open_work=True`) leave them out. Jobs keep their own guard (FR-OB-17).
    historical_column: ClassVar[str | None] = None

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if cls.list_fields:
            cols = {n: getattr(cls.model, n) for n in cls.list_fields}
            cls.sortable = {**cols, **cls.sortable}
            cls.filterable = {**{n: FilterRule(c) for n, c in cols.items()}, **cls.filterable}
        missing = [n for n in cls.suggestable if n not in cls.filterable]
        if missing:
            raise TypeError(f"{cls.__name__}.suggestable: not filterable: {missing}")
        if cls.money_fields and not (cls.money_currency or cls.sum_currency):
            raise TypeError(f"{cls.__name__}.money_fields: no currency field (money_currency or sum_currency)")
        if cls.money_fields and cls.sum_currency is None:
            cls.sum_currency = cls.money_currency  # the totals of the amounts, per their currency
        # What the totals add up: the declared sums, then every amount that is a column of the query.
        cls.totalled = tuple(dict.fromkeys([*cls.summable, *(f for f in cls.money_fields
                                                             if f in cls.sortable and f not in cls.unsummed)]))

    def __init__(self, uow) -> None:
        self.uow = uow

    @property
    def session(self):
        return self.uow.session

    # ── reads ────────────────────────────────────────────────────────────────
    def base_query(self) -> Select:
        """Applies the automatic filters every read goes through."""
        q = select(self.model)
        if issubclass(self.model, SoftDeletable):
            q = q.where(self.model.deleted_at.is_(None))
        return q

    async def get(self, id: int, *, for_update: bool = False) -> T:
        q = self.base_query().where(self.model.id == id)
        if for_update:
            q = q.with_for_update()
        row = (await self.session.execute(q)).scalar_one_or_none()
        if row is None:
            raise NotFound(self.model.__name__, id)
        return row

    async def list(self, spec: QuerySpec, *, open_work: bool = False) -> Page[T]:
        q = self._apply(self.base_query(), spec, open_work=open_work)
        # Counted over the keys only: over the full row, SQL would compute every computed field of
        # every matching row just to count them (8755 orders: 5 s).
        pk = self.model.__mapper__.primary_key[0]
        total = (await self.session.execute(
            select(func.count()).select_from(self._only(q, pk).order_by(None).subquery())
        )).scalar_one()
        groups, truncated = (await self._groups(q, spec)) if spec.group else (None, False)
        totals = (await self._totals(q)) if self.totalled else None
        q = self._order(q, spec)
        offset = (spec.page - 1) * spec.page_size
        if offset:
            # A later page: its keys first, then the rows. With OFFSET over the full row, SQL computes
            # every computed field (correlated subqueries) of each skipped row too (page 100: 7.7 s).
            pk = self.model.__mapper__.primary_key[0]
            keys = self._only(q, pk).offset(offset).limit(spec.page_size).scalar_subquery()
            q = q.where(pk.in_(keys))
        else:
            q = q.limit(spec.page_size)
        items = list((await self.session.execute(q)).scalars().all())
        return Page[Any](items=items, total=total, groups=groups, groups_truncated=truncated, totals=totals)

    async def count(self, spec: QuerySpec) -> int:
        """How many rows the list's query selects: its filters and search, nothing else (no totals, groups or rows)."""
        q = self._apply(self.base_query(), spec, open_work=False)
        pk = self.model.__mapper__.primary_key[0]
        return (await self.session.execute(select(func.count()).select_from(self._only(q, pk).order_by(None).subquery()))).scalar_one()

    async def matching_ids(self, spec: QuerySpec, limit: int) -> list[int]:
        """The ids the list's query selects (its filters and search; no paging), at most `limit`, in its order:
        a record action's "all matching" (platform/bulk_actions.py)."""
        pk = self.model.__mapper__.primary_key[0]
        q = self._order(self._apply(self.base_query(), spec, open_work=False), spec)
        return [r[0] for r in (await self.session.execute(self._only(q, pk).limit(limit))).all()]

    async def suggest(self, field: str, text: str = "") -> list[str]:
        """The values of a field already in the list, most used first; the same rows the list reads."""
        if field not in self.suggestable:
            raise InvalidInput("query.not_suggestable", f"no suggestions for {field!r}",
                               {"field": field, "allowed": list(self.suggestable)})
        col = self.filterable[field].column
        q = self._apply(self.base_query(), QuerySpec(), open_work=False).where(col.is_not(None), col != "")
        if text.strip():
            q = q.where(col.ilike(f"%{_like_escape(text.strip())}%", escape="\\"))
        rows = await self.session.execute(self._only(q, col, func.count()).group_by(col)
                                          .order_by(func.count().desc(), col.asc()).limit(MAX_SUGGESTIONS))
        return [str(r[0]) for r in rows]

    # ── totals (the list footer) ─────────────────────────────────────────────
    def _sum_columns(self) -> tuple[Any, list[Any]]:
        cur = self.sortable[self.sum_currency] if self.sum_currency else None
        return cur, [func.sum(self.sortable[f]) for f in self.totalled]

    def _total_of(self, currency: Any, count: int, sums: Sequence[Any]) -> Total:
        return Total(currency=currency, count=count,
                     sums={f: str(v if v is not None else 0) for f, v in zip(self.totalled, sums)})

    async def _totals(self, q: Select) -> list[Total]:
        """The summable fields' sums over the whole filtered result, per currency."""
        cur, sums = self._sum_columns()
        cols = [cur] if cur is not None else []
        tq = self._only(q, *cols, func.count(), *sums).order_by(None)
        if cur is not None:
            tq = tq.group_by(cur).order_by(cur.asc().nulls_last())
        rows = (await self.session.execute(tq)).all()
        if cur is None:
            return [self._total_of(None, r[0], r[1:]) for r in rows if r[0]]
        return [self._total_of(r[0], r[1], r[2:]) for r in rows]

    # ── grouping (design §13.19) ─────────────────────────────────────────────
    def _group_order(self, spec: QuerySpec) -> list[tuple[Any, Any]]:
        """(column, ORDER BY clause) per group key. Empty values come last in
        either direction, the same in the rows and in the counts."""
        out = []
        for k in spec.group:
            if k.field not in self.groupable:
                raise InvalidInput(
                    "query.not_groupable", f"cannot group by {k.field!r}",
                    {"field": k.field, "allowed": list(self.groupable)},
                )
            col = self.sortable.get(k.field)
            if col is None:
                col = self.filterable[k.field].column
            out.append((col, (col.desc() if k.desc else col.asc()).nulls_last()))
        return out

    async def _groups(self, q: Select, spec: QuerySpec) -> tuple[list[Group], bool]:
        """Each group of the whole filtered result and its row count — so a
        group header shows its true size whichever page it starts on."""
        order = self._group_order(spec)
        cols = [c for c, _ in order]
        gq = (self._only(q, *cols, func.count()).group_by(*cols)
              .order_by(*(o for _, o in order)).limit(MAX_GROUPS + 1))
        rows = (await self.session.execute(gq)).all()
        groups = [Group(key=[_json_key(v) for v in r[:-1]], count=r[-1]) for r in rows[:MAX_GROUPS]]
        if self.totalled and groups:
            await self._group_totals(q, cols, groups)
        return groups, len(rows) > MAX_GROUPS

    async def _group_totals(self, q: Select, cols: list[Any], groups: list[Group]) -> None:
        """Each group's sums, per currency (one more GROUP BY over the same rows)."""
        cur, sums = self._sum_columns()
        by = [*cols, *([cur] if cur is not None else [])]
        rows = (await self.session.execute(
            self._only(q, *by, func.count(), *sums).group_by(*by)
            .order_by(*([cur.asc().nulls_last()] if cur is not None else [])))).all()
        index = {tuple(g.key): g for g in groups}
        n = len(cols)
        for r in rows:
            g = index.get(tuple(_json_key(v) for v in r[:n]))
            if g is None:  # beyond MAX_GROUPS
                continue
            currency = r[n] if cur is not None else None
            rest = r[n + (1 if cur is not None else 0):]
            g.totals = [*(g.totals or []), self._total_of(currency, rest[0], rest[1:])]

    async def add(self, entity: T) -> T:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def soft_delete(self, entity: T) -> T:
        if not isinstance(entity, SoftDeletable):
            raise TypeError(f"{type(entity).__name__} is not SoftDeletable")
        actor = self.session.info.get("actor")
        entity.deleted_at = clock().now()
        entity.deleted_by = actor.user_id if actor else None
        await self.session.flush()
        return entity

    # ── query building ───────────────────────────────────────────────────────
    def _only(self, q: Select, *columns: Any) -> Select:
        """`q` with only these columns, still FROM the model's table. A computed field is a correlated
        subquery: with no plain column of the table left (and no filter naming it), SQL would lose the
        table, the subquery would no longer correlate and would read every row ("more than one row
        returned by a subquery")."""
        return q.with_only_columns(*columns).select_from(self.model)

    def _apply(self, q: Select, spec: QuerySpec, *, open_work: bool) -> Select:
        if self.historical_column is not None:
            col = getattr(self.model, self.historical_column)
            if open_work:
                q = q.where(col.is_(False))
        if spec.search and (self.search_fields or self.search_numbers):
            text = spec.search.strip()
            pattern = f"%{_like_escape(text)}%"
            number = int(text) if text.isdigit() and len(text) <= 18 else None
            q = q.where(or_(
                *(getattr(self.model, f).ilike(pattern, escape="\\") for f in self.search_fields),
                *(getattr(self.model, f) == number for f in self.search_numbers if number is not None),
            ))
        for f in [*spec.scope, *spec.filters]:
            q = q.where(self._filter(f))
        return q

    async def outside_scope(self, ids: Sequence[int], scope: Sequence[Filter]) -> list[int]:
        """Of these ids, those the scope does not select (a scoped page's action on records it never showed)."""
        if not scope:
            return []
        pk = self.model.__mapper__.primary_key[0]
        q = self._apply(self.base_query(), QuerySpec(scope=list(scope)), open_work=False).where(pk.in_(list(ids)))
        inside = {r[0] for r in (await self.session.execute(self._only(q, pk))).all()}
        return sorted(set(ids) - inside)

    # ── field kinds and operators (one declaration: query and /meta) ────────
    @classmethod
    def field_kind(cls, name: str) -> str:
        if name in cls.enums:
            return "enum"
        rule = cls.filterable.get(name)
        if rule is not None and rule.kind:
            return rule.kind
        column = rule.column if rule is not None else cls.sortable.get(name, getattr(cls.model, name, None))
        return kind_for(column)

    @classmethod
    def field_ops(cls, name: str) -> tuple[str, ...]:
        rule = cls.filterable.get(name)
        if rule is None:
            return ()
        if rule.ops is not None:  # a narrower set declared on purpose
            return tuple(sorted("between" if o == "range" else o for o in rule.ops))
        return OPS_BY_KIND[cls.field_kind(name)]

    @classmethod
    def unknown_view_columns(cls, read_schema: type[BaseModel] | None = None) -> list[str]:
        """Column ids the system views name that are neither a field nor a declared client column."""
        known = {f["name"] for f in cls.field_meta(read_schema)} | set(cls.client_columns) | set(cls.meta_hidden)
        named = {c for p in cls.presets for c in [*p.state.get("columns", []), *p.state.get("hidden", [])]}
        return sorted(named - known)

    @classmethod
    def field_meta(cls, read_schema: type[BaseModel] | None = None) -> list[dict[str, Any]]:
        """Every field a client can show, filter or sort: the read schema's
        scalar fields, then any other declared key. JSON / list fields are
        left out (nothing to filter, sort or show in a cell)."""
        names: list[str] = []
        for name, info in (read_schema.model_fields.items() if read_schema else ()):
            if _scalar(info.annotation):
                names.append(name)
        in_row = set(names)  # each row carries these: a client may show them as columns
        currency = cls.money_currency or cls.sum_currency
        names = [n for n in dict.fromkeys([*names, *cls.filterable, *cls.sortable]) if n not in cls.meta_hidden]  # each once, schema order first
        out = []
        for n in names:
            out.append({
                "name": n, "label": cls.labels.get(n) or _humanise(n), "type": cls.field_kind(n),
                "enum_values": list(cls.enums.get(n, ())), "filterable": n in cls.filterable,
                "sortable": n in cls.sortable, "groupable": n in cls.groupable, "operators": list(cls.field_ops(n)),
                "in_row": n in in_row,  # else a key to filter or sort on only: never offered as a column
                **({"suggestable": True} if n in cls.suggestable else {}),
                **({"summable": True} if n in cls.summable else {}),
                **({"money": True, "currency_field": currency} if n in cls.money_fields else {}),
                "group": cls.group_of(n),
                **({"ref": r.as_dict(n)} if (r := cls.refs.get(n)) is not None else {}),
            })
        return out

    @classmethod
    def group_of(cls, name: str) -> str:
        return next((label for label, names in cls.field_groups if name in names), OTHER)

    @classmethod
    def group_labels(cls) -> list[str]:
        labels = [label for label, _ in cls.field_groups]
        return labels if OTHER in labels else [*labels, OTHER]

    def _filter(self, f: Filter):
        cond = self._condition(f)
        rule = self.filterable[f.field]
        return rule.through(cond) if rule.through is not None else cond

    def _condition(self, f: Filter):
        rule = self.filterable.get(f.field)
        if rule is None:
            raise InvalidInput(
                "query.unknown_field", f"cannot filter on {f.field!r}",
                {"field": f.field, "allowed": sorted(self.filterable)},
            )
        allowed = self.field_ops(f.field)
        op = "between" if f.op == "range" else f.op
        if op not in allowed:
            raise InvalidInput(
                "query.unsupported_operator", f"{f.op!r} is not allowed on {f.field!r}",
                {"field": f.field, "op": f.op, "allowed": list(allowed)},
            )
        col = rule.column
        if op in NO_VALUE_OPS:
            return col.is_(None) if op == "is_null" else col.is_not(None)
        if op == "eq" and is_relative(f.value) and self.field_kind(f.field) in ("date", "datetime"):
            first, last = period(f.value, clock().today())  # the business day, Toronto
            if self.field_kind(f.field) == "date":
                return and_(col >= first, col <= last)
            return and_(col >= clock().at(first, time(0)), col < clock().at(last + timedelta(days=1), time(0)))
        if op in NO_VALUE_OPS:
            return col.is_(None) if op == "is_null" else col.is_not(None)
        try:
            if op in RANGE_OPS:
                low, high = (list(f.value) + [None, None])[:2]
                parts = []
                if low is not None:
                    parts.append(col >= rule.convert(low))
                if high is not None:
                    parts.append(col <= rule.convert(high))
                return and_(*parts) if parts else true()
            values = f.value if isinstance(f.value, list) else [f.value]
            if op in ("contains", "not_contains", "starts_with", "ends_with"):
                pats = [{"contains": "%{}%", "not_contains": "%{}%", "starts_with": "{}%", "ends_with": "%{}"}[op]
                        .format(_like_escape(str(v))) for v in values]
                likes = [col.ilike(p, escape="\\") for p in pats]
                if op == "not_contains":  # none of them; an empty value does not contain anything
                    return or_(col.is_(None), not_(or_(*likes)))
                return or_(*likes)
            converted = [rule.convert(v) for v in values]
            if op in ("eq", "in"):
                return col.in_(converted) if len(converted) > 1 or op == "in" else col == converted[0]
            if op in ("ne", "not_in"):  # none of them; NULL is not any of them either
                return or_(col.is_(None), col.not_in(converted))
            return {"gt": col > converted[0], "gte": col >= converted[0],
                    "lt": col < converted[0], "lte": col <= converted[0]}[op]
        except (ValueError, TypeError, ArithmeticError) as e:
            raise InvalidInput(
                "query.bad_value", f"bad value for {f.field!r}: {e}", {"field": f.field},
            ) from e

    def _order(self, q: Select, spec: QuerySpec) -> Select:
        keys = spec.sort or parse_sort(",".join(self.default_sort))
        clauses = [o for _, o in self._group_order(spec)]  # grouped rows first, then the sort
        for k in keys:
            col = self.sortable.get(k.field)
            if col is None:
                raise InvalidInput(
                    "query.unknown_field", f"cannot sort on {k.field!r}",
                    {"field": k.field, "allowed": sorted(self.sortable)},
                )
            clauses.append(col.desc() if k.desc else col.asc())
        # Stable paging: equal sort values never swap between pages.
        clauses.append(self.model.id.desc() if not keys or keys[-1].desc else self.model.id.asc())
        return q.order_by(*clauses)



def _humanise(name: str) -> str:
    """`source_order_number` → "Source order number"; `id` words → "ID"."""
    words = ["ID" if w == "id" else w for w in name.split("_") if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _scalar(annotation: Any) -> bool:
    """str / int / Decimal / bool / date / datetime, a str enum or a Literal, optionally `| None`."""
    args = get_args(annotation) if get_origin(annotation) in (Union, UnionType) else (annotation,)
    args = tuple(a for a in args if a is not type(None))
    if len(args) == 1 and get_origin(args[0]) is Literal:
        return True
    return len(args) == 1 and isinstance(args[0], type) and (
        args[0] in (int, float, Decimal, bool, date, datetime) or issubclass(args[0], str))


def _json_key(v: Any) -> Any:
    """A group key value as JSON carries it (dates ISO, decimals as text)."""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v
