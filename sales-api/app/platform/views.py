"""Saved list views (design §13.19): a user's own named states of a list —
columns, order, hidden, sort, group, filters, search — plus the system views
each repository declares (`presets`, returned in /meta).

A view belongs to one user: every read and write is scoped to the caller's
id, so another user's view is simply not found (404). Using the views of a
list needs that list's read permission. The state is the client's versioned
`ViewState` JSON; it is checked against the current version and the list's
fields when saved, and flagged `stale` when read if it no longer fits (an
older version, or a field the list no longer has).

The table is the business code's model with `UserViewMixin`'s columns; its
name is chosen there.
"""
# No `from __future__ import annotations`: FastAPI must see the real types of
# views_router's closures (the dependencies and the DTOs).
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import Boolean, Integer, String, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.authorization import authorize
from app.platform.entities import Audited, Versioned
from app.platform.errors import BusinessError, InvalidInput, NotFound
from app.platform.query import MAX_FILTERS, MAX_GROUP_KEYS, MAX_PAGE_SIZE

VIEW_STATE_VERSION = 1
MAX_VIEWS_PER_LIST = 50
_STATE_KEYS = {"version", "sort", "group", "filters", "search", "pageSize", "columns", "hidden", "includeHistorical"}


class UserViewMixin(Audited, Versioned):
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


@dataclass(frozen=True)
class Preset:
    """A system view: a stable key, its name and the parts of the state it sets."""
    key: str
    name: str
    state: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "state": {"version": VIEW_STATE_VERSION, **self.state}}


# ── the state ────────────────────────────────────────────────────────────────
# Older versions with a trivial upgrade: {from_version: fn(state) -> state}.
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _names(entries: Any, key: str) -> list[str]:
    if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get(key), str) for e in entries):
        raise ValueError(f"must be a list of objects with a {key!r}")
    return [e[key] for e in entries]


def check_state(state: Any, repo: Any) -> dict[str, Any]:
    """The state as stored: the current version (a trivially older one is
    upgraded), only known keys, and only fields this list has. Raises
    InvalidInput otherwise."""
    if not isinstance(state, dict) or not isinstance(state.get("version"), int):
        raise InvalidInput("view.bad_state", "the view's state has no version", {"field": "state"})
    v = state["version"]
    while v < VIEW_STATE_VERSION and v in MIGRATIONS:
        state, v = MIGRATIONS[v](state), v + 1
    if v != VIEW_STATE_VERSION:
        raise InvalidInput("view.bad_version", f"the view's state is version {state['version']}; this server reads "
                           f"{VIEW_STATE_VERSION}", {"field": "state"})
    unknown = set(state) - _STATE_KEYS
    if unknown:
        raise InvalidInput("view.bad_state", f"unknown parts of the state: {', '.join(sorted(unknown))}", {"field": "state"})
    out = {"version": VIEW_STATE_VERSION, **{k: state[k] for k in _STATE_KEYS - {"version"} if k in state}}
    try:
        sort = _names(out.get("sort", []), "field")
        group = _names(out.get("group", []), "field")
        filters = _names(out.get("filters", []), "field")
        columns = list(out.get("columns", [])) + list(out.get("hidden", []))
        if not all(isinstance(c, str) for c in columns):
            raise ValueError("columns and hidden are lists of column ids")
        if len(group) > MAX_GROUP_KEYS or len(filters) > MAX_FILTERS:
            raise ValueError("too many group keys or filters")
        size = out.get("pageSize", 50)
        if not isinstance(size, int) or not 1 <= size <= MAX_PAGE_SIZE:
            raise ValueError(f"pageSize is 1…{MAX_PAGE_SIZE}")
        if out.get("search") is not None and not isinstance(out["search"], str):
            raise ValueError("search is text")
    except ValueError as e:
        raise InvalidInput("view.bad_state", f"the view's state is not valid: {e}", {"field": "state"}) from e
    missing = (set(sort) - set(repo.sortable)) | (set(group) - set(repo.groupable)) | (set(filters) - set(repo.filterable))
    if missing:
        raise InvalidInput("view.unknown_field", f"this list has no field {', '.join(sorted(missing))}",
                           {"field": "state", "fields": sorted(missing)})
    return out


def is_stale(state: dict[str, Any], repo: Any) -> bool:
    try:
        return check_state(state, repo) != state
    except InvalidInput:
        return True


# ── DTOs ─────────────────────────────────────────────────────────────────────
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str = Field(max_length=100)
    name: Name
    state: dict[str, Any]


class ViewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    state: dict[str, Any] | None = None


class ViewRead(BaseModel):
    id: int
    resource: str
    name: str
    state: dict[str, Any]
    is_default: bool
    sort: int
    version: int
    # Saved by an older version or naming a field the list no longer has:
    # the client says so and asks the user to review and save.
    stale: bool


# ── the service ──────────────────────────────────────────────────────────────
class ViewService:
    """`lists` maps a resource key (the list's path, e.g. "orders") to its
    read service class; only those lists have views."""
    model: ClassVar[type]
    lists: ClassVar[dict[str, type]]

    def __init__(self, uow) -> None:
        self.uow = uow
        self.s = uow.session

    async def _list(self, resource: str):
        svc = self.lists.get(resource)
        if svc is None:
            raise InvalidInput("view.unknown_resource", f"no list {resource!r}", {"field": "resource"})
        await authorize(self.uow, svc.read_permission)
        return svc

    def _user(self) -> uuid.UUID:
        uid = self.uow.actor.user_id
        if uid is None:  # the system actor has no views
            raise NotFound("UserView", "-")
        return uid

    def _read(self, v, repo) -> ViewRead:
        return ViewRead(id=v.id, resource=v.resource, name=v.name, state=v.state, is_default=v.is_default,
                        sort=v.sort, version=v.version, stale=is_stale(v.state, repo))

    async def _own(self, id: int, *, for_update: bool = False):
        m = self.model
        q = select(m).where(m.id == id, m.user_id == self._user())
        if for_update:
            q = q.with_for_update()
        v = (await self.s.execute(q)).scalar_one_or_none()
        if v is None:
            raise NotFound("UserView", id)  # another user's view is not found either
        return v

    async def _name_free(self, resource: str, name: str, *, but: int | None = None) -> None:
        m = self.model
        q = select(m.id).where(m.user_id == self._user(), m.resource == resource, func.lower(m.name) == name.lower())
        if but is not None:
            q = q.where(m.id != but)
        if (await self.s.execute(q.limit(1))).scalar_one_or_none() is not None:
            raise BusinessError("view.name_taken", f"you already have a view named {name!r}", {"field": "name"})

    async def list(self, resource: str) -> list[ViewRead]:
        svc = await self._list(resource)
        m = self.model
        rows = (await self.s.execute(select(m).where(m.user_id == self._user(), m.resource == resource)
                                     .order_by(m.sort, func.lower(m.name)))).scalars()
        return [self._read(v, svc.repo_cls) for v in rows]

    async def create(self, data: ViewCreate) -> ViewRead:
        svc = await self._list(data.resource)
        state = check_state(data.state, svc.repo_cls)
        await self._name_free(data.resource, data.name)
        m = self.model
        count = (await self.s.execute(select(func.count()).where(m.user_id == self._user(), m.resource == data.resource))).scalar_one()
        if count >= MAX_VIEWS_PER_LIST:
            raise BusinessError("view.too_many", f"at most {MAX_VIEWS_PER_LIST} views per list")
        v = m(user_id=self._user(), resource=data.resource, name=data.name, state=state, is_default=False, sort=count,
              version=1)
        self.s.add(v)
        await self._flush()
        return self._read(v, svc.repo_cls)

    async def update(self, id: int, data: ViewUpdate, version: int | None) -> ViewRead:
        v = await self._own(id, for_update=True)
        svc = await self._list(v.resource)
        if version is not None and version != v.version:
            raise BusinessError("entity.stale", "the view was changed elsewhere; reload it", {"version": v.version})
        if data.name is not None and data.name != v.name:
            await self._name_free(v.resource, data.name, but=v.id)
            v.name = data.name
        if data.state is not None:
            v.state = check_state(data.state, svc.repo_cls)
        await self._flush()
        return self._read(v, svc.repo_cls)

    async def delete(self, id: int) -> None:
        v = await self._own(id, for_update=True)
        await self._list(v.resource)
        await self.s.delete(v)
        await self.s.flush()

    async def set_default(self, id: int, on: bool = True) -> ViewRead:
        """The view this user's list opens with; at most one per list."""
        v = await self._own(id, for_update=True)
        svc = await self._list(v.resource)
        m = self.model
        if on:
            await self.s.execute(update(m).where(m.user_id == v.user_id, m.resource == v.resource, m.id != v.id,
                                                 m.is_default.is_(True)).values(is_default=False))
        v.is_default = on
        await self._flush()
        return self._read(v, svc.repo_cls)

    async def _flush(self) -> None:
        try:
            await self.s.flush()
        except IntegrityError as e:  # a race on the unique name or the single default
            raise BusinessError("view.conflict", "another change to your views came first; reload them") from e


# ── HTTP ─────────────────────────────────────────────────────────────────────
def views_router(service_cls: type[ViewService], *, uow: Callable[..., Any]) -> APIRouter:
    """`/views`: the caller's own views of one list. Each write commits once."""
    r = APIRouter(prefix="/views", tags=["views"])
    UoW = Annotated[Any, Depends(uow)]

    @r.get("", response_model=list[ViewRead])
    async def list_(u: UoW, resource: Annotated[str, Query(max_length=100)]):
        return await service_cls(u).list(resource)

    @r.post("", response_model=ViewRead, status_code=201)
    async def create(u: UoW, data: ViewCreate):
        out = await service_cls(u).create(data)
        await u.commit()
        return out

    @r.put("/{id}", response_model=ViewRead)
    async def update_(u: UoW, id: int, data: ViewUpdate, version: Annotated[int | None, Query()] = None):
        out = await service_cls(u).update(id, data, version)
        await u.commit()
        return out

    @r.delete("/{id}", status_code=204)
    async def delete(u: UoW, id: int):
        await service_cls(u).delete(id)
        await u.commit()
        return Response(status_code=204)

    @r.post("/{id}/default", response_model=ViewRead)
    async def default(u: UoW, id: int, on: bool = True):
        out = await service_cls(u).set_default(id, on)
        await u.commit()
        return out

    return r
