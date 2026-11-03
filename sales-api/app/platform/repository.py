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

The same declaration drives the list query and `/meta` (field_meta), from
which the client builds its column menus and search panel.

There is deliberately no `delete`: config rows are deactivated, documents are
never removed. Models that opt in to SoftDeletable get `soft_delete`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal
from types import UnionType
from typing import Any, ClassVar, Generic, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import Select, and_, func, not_, or_, select, true

from app.platform.entities import SoftDeletable
from app.platform.errors import InvalidInput, NotFound
from app.platform.views import Preset
from app.platform.query import (  # noqa: F401 — FilterRule, parse_bool re-exported
    MAX_GROUPS, NO_VALUE_OPS, OPS_BY_KIND, RANGE_OPS, Filter, FilterRule, Group, Page, QuerySpec, Total, kind_for,
    parse_bool, parse_sort,
)

T = TypeVar("T")


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Repository(Generic[T]):
    model: ClassVar[type]
    search_fields: ClassVar[tuple[str, ...]] = ()
    # Integer columns a search of digits only matches exactly (an internal number).
    search_numbers: ClassVar[tuple[str, ...]] = ()
    sortable: ClassVar[Mapping[str, Any]] = {}
    filterable: ClassVar[Mapping[str, FilterRule]] = {}
    # Model columns that are both sortable and filterable (merged into the two
    # maps above when the class is defined; an explicit entry there wins).
    list_fields: ClassVar[tuple[str, ...]] = ()
    labels: ClassVar[Mapping[str, str]] = {}
    enums: ClassVar[Mapping[str, Sequence[str]]] = {}
    # Fields the list can be grouped by (sortable or filterable keys): the
    # values that repeat — a status, a partner, a flag — not free text.
    groupable: ClassVar[tuple[str, ...]] = ()
    # Numeric fields (sortable keys) summed over the whole filtered result —
    # the list footer and the group headers — per value of `sum_currency`.
    summable: ClassVar[tuple[str, ...]] = ()
    sum_currency: ClassVar[str | None] = None
    # System views (design §13.19): named partial view states, in /meta.
    presets: ClassVar[tuple[Preset, ...]] = ()
    default_sort: ClassVar[tuple[str, ...]] = ()  # sortable keys, "-" for descending
    # A boolean column marking rows imported as history (e.g. from the system
    # being replaced). Excluded unless asked for; never on open-work lists.
    historical_column: ClassVar[str | None] = None

    def __init_subclass__(cls, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if cls.list_fields:
            cols = {n: getattr(cls.model, n) for n in cls.list_fields}
            cls.sortable = {**cols, **cls.sortable}
            cls.filterable = {**{n: FilterRule(c) for n, c in cols.items()}, **cls.filterable}

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
        total = (await self.session.execute(
            select(func.count()).select_from(q.order_by(None).subquery())
        )).scalar_one()
        groups, truncated = (await self._groups(q, spec)) if spec.group else (None, False)
        totals = (await self._totals(q)) if self.summable else None
        q = self._order(q, spec).offset((spec.page - 1) * spec.page_size).limit(spec.page_size)
        items = list((await self.session.execute(q)).scalars().all())
        return Page[Any](items=items, total=total, groups=groups, groups_truncated=truncated, totals=totals)

    # ── totals (the list footer) ─────────────────────────────────────────────
    def _sum_columns(self) -> tuple[Any, list[Any]]:
        cur = self.sortable[self.sum_currency] if self.sum_currency else None
        return cur, [func.sum(self.sortable[f]) for f in self.summable]

    def _total_of(self, currency: Any, count: int, sums: Sequence[Any]) -> Total:
        return Total(currency=currency, count=count,
                     sums={f: str(v if v is not None else 0) for f, v in zip(self.summable, sums)})

    async def _totals(self, q: Select) -> list[Total]:
        """The summable fields' sums over the whole filtered result, per currency."""
        cur, sums = self._sum_columns()
        cols = [cur] if cur is not None else []
        tq = q.with_only_columns(*cols, func.count(), *sums).order_by(None)
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
        gq = (q.with_only_columns(*cols, func.count()).group_by(*cols)
              .order_by(*(o for _, o in order)).limit(MAX_GROUPS + 1))
        rows = (await self.session.execute(gq)).all()
        groups = [Group(key=[_json_key(v) for v in r[:-1]], count=r[-1]) for r in rows[:MAX_GROUPS]]
        if self.summable and groups:
            await self._group_totals(q, cols, groups)
        return groups, len(rows) > MAX_GROUPS

    async def _group_totals(self, q: Select, cols: list[Any], groups: list[Group]) -> None:
        """Each group's sums, per currency (one more GROUP BY over the same rows)."""
        cur, sums = self._sum_columns()
        by = [*cols, *([cur] if cur is not None else [])]
        rows = (await self.session.execute(
            q.with_only_columns(*by, func.count(), *sums).group_by(*by)
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
        entity.deleted_at = datetime.now(timezone.utc)
        entity.deleted_by = actor.user_id if actor else None
        await self.session.flush()
        return entity

    # ── query building ───────────────────────────────────────────────────────
    def _apply(self, q: Select, spec: QuerySpec, *, open_work: bool) -> Select:
        if self.historical_column is not None:
            col = getattr(self.model, self.historical_column)
            if open_work or not spec.include_historical:
                q = q.where(col.is_(False))
        if spec.search and (self.search_fields or self.search_numbers):
            text = spec.search.strip()
            pattern = f"%{_like_escape(text)}%"
            number = int(text) if text.isdigit() and len(text) <= 18 else None
            q = q.where(or_(
                *(getattr(self.model, f).ilike(pattern, escape="\\") for f in self.search_fields),
                *(getattr(self.model, f) == number for f in self.search_numbers if number is not None),
            ))
        for f in spec.filters:
            q = q.where(self._filter(f))
        return q

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
    def field_meta(cls, read_schema: type[BaseModel] | None = None) -> list[dict[str, Any]]:
        """Every field a client can show, filter or sort: the read schema's
        scalar fields, then any other declared key. JSON / list fields are
        left out (nothing to filter, sort or show in a cell)."""
        names: list[str] = []
        for name, info in (read_schema.model_fields.items() if read_schema else ()):
            if _scalar(info.annotation):
                names.append(name)
        names = list(dict.fromkeys([*names, *cls.filterable, *cls.sortable]))  # each once, schema order first
        out = []
        for n in names:
            out.append({
                "name": n, "label": cls.labels.get(n) or _humanise(n), "type": cls.field_kind(n),
                "enum_values": list(cls.enums.get(n, ())), "filterable": n in cls.filterable,
                "sortable": n in cls.sortable, "groupable": n in cls.groupable, "operators": list(cls.field_ops(n)),
                **({"summable": True} if n in cls.summable else {}),
            })
        return out

    def _filter(self, f: Filter):
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
