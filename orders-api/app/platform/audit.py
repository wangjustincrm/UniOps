"""Audit trail (design §13.13).

`AuditTrail(model)` attached to a session does two things on every flush:

- fills `created_by*` / `updated_by*` / `updated_at` on CreationAudited and
  Audited rows from the session's actor (`session.info["actor"]`); a flush of
  such a row without an actor is a bug and raises;
- writes one audit row per inserted / changed / deleted Audited entity with
  the changed fields before and after, the actor and the command's reason
  (`session.info["audit_reason"]`);
- for any other model, writes an update row when one of its
  `__audited_fields__` changes — the fields a state machine registered with
  `bind` — so every status transition is audited, documents and ACKs too
  (§13.6).

The audit table is the business code's model with `AuditRecordMixin`'s columns;
its name is chosen there, not here.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, event, inspect, insert
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.platform.entities import Audited, CreationAudited


class AuditRecordMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # create | update | delete
    entity: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


# Columns the trail fills itself; recording them as "changes" is noise.
_BOOKKEEPING = {
    "created_at", "created_by", "created_by_name",
    "updated_at", "updated_by", "updated_by_name", "version",
}


def jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, enum.Enum):
        return jsonable(v.value)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    return str(v)


def _columns(obj: Any) -> list[str]:
    return [a.key for a in inspect(obj).mapper.column_attrs]


def _snapshot(obj: Any) -> dict[str, Any]:
    return {k: jsonable(getattr(obj, k)) for k in _columns(obj) if k not in _BOOKKEEPING}


def _changes(obj: Any, only: frozenset[str] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    state = inspect(obj)
    before, after = {}, {}
    for k in _columns(obj):
        if k in _BOOKKEEPING or (only is not None and k not in only):
            continue
        h = state.attrs[k].history
        if h.has_changes():
            before[k] = jsonable(h.deleted[0]) if h.deleted else None
            after[k] = jsonable(h.added[0]) if h.added else None
    return before, after


class AuditTrail:
    def __init__(self, model: type) -> None:
        self.model = model

    def attach(self, session: Session) -> None:
        """`session` is a sync Session (for an AsyncSession: `.sync_session`)."""
        if session.info.get("_audit_attached"):
            return
        session.info["_audit_attached"] = True
        event.listen(session, "before_flush", self._before_flush)
        event.listen(session, "after_flush", self._after_flush)

    def _before_flush(self, session: Session, flush_context, instances) -> None:  # noqa: ARG002
        actor = session.info.get("actor")
        now = datetime.now(timezone.utc)
        pending: list[tuple[str, Any, dict | None]] = session.info.setdefault("_audit_pending", [])

        for obj in list(session.new):
            if isinstance(obj, CreationAudited):
                _require_actor(actor, obj)
                obj.created_by = actor.user_id
                obj.created_by_name = actor.name
            if isinstance(obj, Audited):
                pending.append(("create", obj, None))

        for obj in list(session.dirty):
            if not session.is_modified(obj, include_collections=False):
                continue
            if not isinstance(obj, Audited):
                fields = getattr(type(obj), "__audited_fields__", None)
                if fields:
                    before, after = _changes(obj, only=fields)
                    if after:
                        _require_actor(actor, obj)
                        pending.append(("update", obj, {"before": before, "after": after}))
                continue
            before, after = _changes(obj)
            if not after:
                continue
            _require_actor(actor, obj)
            obj.updated_at = now
            obj.updated_by = actor.user_id
            obj.updated_by_name = actor.name
            pending.append(("update", obj, {"before": before, "after": after}))

        for obj in list(session.deleted):
            if isinstance(obj, Audited):
                _require_actor(actor, obj)
                pending.append(("delete", obj, {"before": _snapshot(obj), "after": None}))

    def _after_flush(self, session: Session, flush_context) -> None:  # noqa: ARG002
        pending = session.info.pop("_audit_pending", [])
        if not pending:
            return
        actor = session.info.get("actor")
        reason = session.info.get("audit_reason")
        now = datetime.now(timezone.utc)
        rows = []
        for action, obj, change in pending:
            if action == "create":
                before, after = None, _snapshot(obj)
            else:
                before, after = change["before"], change["after"]
            ident = inspect(obj).mapper.primary_key_from_instance(obj)
            rows.append({
                "at": now,
                "user_id": actor.user_id,
                "user_name": actor.name,
                "action": action,
                "entity": obj.__tablename__,
                "entity_id": ",".join(str(i) for i in ident) if all(i is not None for i in ident) else None,
                "before": before,
                "after": after,
                "reason": reason,
            })
        # Core insert on the flush's own connection: adding ORM objects is not
        # allowed while a flush is finishing.
        session.connection().execute(insert(self.model.__table__), rows)


def _require_actor(actor: Any, obj: Any) -> None:
    if actor is None:
        raise RuntimeError(
            f"{type(obj).__name__} flushed without an actor — open the unit of work "
            "with the user's Actor or SYSTEM_ACTOR",
        )
