"""Repository (design §13.3).

    class PartnerRepository(Repository[Partner]):
        model = Partner
        search_fields = ("code", "name")
        sortable = {"code": Partner.code, "name": Partner.name}
        filterable = {"active": FilterRule(Partner.active, cast=parse_bool)}

There is deliberately no `delete`: config rows are deactivated, documents are
never removed. Models that opt in to SoftDeletable get `soft_delete`.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, ClassVar, Generic, TypeVar

from sqlalchemy import Select, and_, func, or_, select, true

from app.platform.entities import SoftDeletable
from app.platform.errors import InvalidInput, NotFound
from app.platform.query import Filter, FilterRule, Page, QuerySpec, parse_bool, parse_sort  # noqa: F401 — re-exported

T = TypeVar("T")


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Repository(Generic[T]):
    model: ClassVar[type]
    search_fields: ClassVar[tuple[str, ...]] = ()
    sortable: ClassVar[Mapping[str, Any]] = {}
    filterable: ClassVar[Mapping[str, FilterRule]] = {}
    default_sort: ClassVar[tuple[str, ...]] = ()  # sortable keys, "-" for descending
    # A boolean column marking rows imported as history (e.g. from the system
    # being replaced). Excluded unless asked for; never on open-work lists.
    historical_column: ClassVar[str | None] = None

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
        q = self._order(q, spec).offset((spec.page - 1) * spec.page_size).limit(spec.page_size)
        items = list((await self.session.execute(q)).scalars().all())
        return Page[Any](items=items, total=total)

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
        if spec.search and self.search_fields:
            pattern = f"%{_like_escape(spec.search.strip())}%"
            q = q.where(or_(*(
                getattr(self.model, f).ilike(pattern, escape="\\") for f in self.search_fields
            )))
        for f in spec.filters:
            q = q.where(self._filter(f))
        return q

    def _filter(self, f: Filter):
        rule = self.filterable.get(f.field)
        if rule is None:
            raise InvalidInput(
                "query.unknown_field", f"cannot filter on {f.field!r}",
                {"field": f.field, "allowed": sorted(self.filterable)},
            )
        if f.op not in rule.ops:
            raise InvalidInput(
                "query.unsupported_operator", f"{f.op!r} is not allowed on {f.field!r}",
                {"field": f.field, "op": f.op, "allowed": sorted(rule.ops)},
            )
        try:
            if f.op == "eq":
                return rule.column == rule.convert(f.value)
            if f.op == "in":
                values = f.value if isinstance(f.value, list) else [f.value]
                return rule.column.in_([rule.convert(v) for v in values])
            if f.op == "contains":
                return rule.column.ilike(f"%{_like_escape(str(f.value))}%", escape="\\")
            low, high = (list(f.value) + [None, None])[:2]
            parts = []
            if low is not None:
                parts.append(rule.column >= rule.convert(low))
            if high is not None:
                parts.append(rule.column <= rule.convert(high))
            return and_(*parts) if parts else true()
        except (ValueError, TypeError, ArithmeticError) as e:
            raise InvalidInput(
                "query.bad_value", f"bad value for {f.field!r}: {e}", {"field": f.field},
            ) from e

    def _order(self, q: Select, spec: QuerySpec) -> Select:
        keys = spec.sort or parse_sort(",".join(self.default_sort))
        clauses = []
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

