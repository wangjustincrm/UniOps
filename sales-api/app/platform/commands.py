"""Loading the record a command changes (review 2026-11-10): one way for every service, so the
answers are the same everywhere — `entity.not_found` (404), `entity.version_required` (422) and
`entity.stale` (409) with the versions. A command a person sends names the version they saw;
leaving it out is refused, not taken as "whatever is there". A system caller that acts on what is
there now (a resend from the file register, a 945 row decision) passes ANY_VERSION, on purpose."""
from __future__ import annotations

from typing import Any, Final, TypeVar

from sqlalchemy import select

from app.platform.clock import clock
from app.platform.errors import BusinessError, InvalidInput, NotFound


class _AnyVersion:
    def __repr__(self) -> str:
        return "ANY_VERSION"


ANY_VERSION: Final = _AnyVersion()
VersionArg = int | None | _AnyVersion
T = TypeVar("T")


class Stale(BusinessError):
    def __init__(self, what: str, current: int, sent: int) -> None:
        super().__init__("entity.stale", f"{what} was changed by someone else; reload it",
                         {"current_version": current, "sent_version": sent}, status_code=409)


def check_version(entity: Any, version: VersionArg, what: str = "this record") -> None:
    if version is ANY_VERSION:
        return
    if version is None:
        raise InvalidInput("entity.version_required", "send the version you edited", {"field": "version"})
    if entity.version != version:
        raise Stale(what, entity.version, version)  # type: ignore[arg-type]


MIN_REASON = 3


def require_reason(text: str | None, *, code: str = "reason.required", message: str = "say why (a few words)",
                   field: str = "reason") -> str:
    """A reason a person must give, trimmed; without one (or with fewer than MIN_REASON characters) a 422 naming the field."""
    why = (text or "").strip()
    if len(why) < MIN_REASON:
        raise BusinessError(code, message, {"field": field}, status_code=422)
    return why


def touch(uow: Any, entity: Any) -> None:
    """The record changed through its parts (a line, a price) and not a column of its own: its version moves with the
    unit of work's flush (the mapper's version_id_col), so whoever saw it before is refused, as for any change. Who and
    when are kept on the record; no audit row (no audited field changed: the parts have theirs)."""
    entity.updated_at = clock().now()
    entity.updated_by, entity.updated_by_name = uow.actor.user_id, uow.actor.name


async def lock(session, model: type[T], id: int) -> T:
    """The row, locked, and read again under its lock: for a unit that has it already, read before it could be locked. (A plain
    get(with_for_update=True) of a row in memory raises StaleDataError when it moved meanwhile.)"""
    return (await session.execute(select(model).where(model.id == id).with_for_update().execution_options(populate_existing=True))).scalar_one()


async def load_for_command(session, model: type[T], id: int, *, version: VersionArg, what: str | None = None,
                           for_update: bool = True) -> T:
    """The row, locked, at the version the caller saw."""
    entity = await session.get(model, id, with_for_update=for_update)
    if entity is None:
        raise NotFound(model.__name__, id)
    check_version(entity, version, what or f"this {model.__name__.lower()}")
    return entity
