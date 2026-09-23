"""Recording every field change, at the flush rather than in each write path.

Thirty-odd endpoints write these tables. A line added to each is a line that
will be missing from the next one written, and a trail with a hole in it is
worse than none, because it reads as a complete account of what happened.

Three rules decide what is written:

*Which tables.* This module's own — `ehs_*`. The mirrors (users, tasks,
locations) belong to other services and are written by their syncs; logging
those here would fill the trail with rows nobody in Safety caused.

*Which fields.* Everything except the housekeeping columns and the ones whose
value is not readable anyway — a base64 signature, a file's storage key. That
a signature was replaced is the fact worth having; the bytes are not.

*Which values.* Health information under PHIPA is recorded as having changed
and not as what it changed to. A general log carries a general permission, and
copying an allergy history into it would put that history behind a weaker gate
than the one the medical table exists to provide. The field name and the time
still answer "was this record altered, by whom, when" — which is what an audit
trail is for.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.actor import current_actor
from app.models.change_log import ChangeLogEntry

# Not logged: written by every save and saying nothing about what changed.
_HOUSEKEEPING = {"created_at", "updated_at"}

# Logged as "changed", never by value: too large to read, or meaningless out of
# context. A storage key tells an auditor nothing a filename does not.
_OPAQUE_SUFFIXES = ("signature", "_file_id", "storage_key", "qr_token", "password")

# Tables whose values are health information under PHIPA. The change is
# recorded; the value is not.
_SENSITIVE_TABLES = {
    "ehs_incident_medical",
    "ehs_worker_profiles",
    "ehs_first_aid_entries",
}

# How much of a value is kept. Long enough for a sentence somebody would read
# back, short enough that a pasted procedure does not fill the table.
_MAX_VALUE = 500

# Where a record's own number lives, so a trail can be read without joining
# back to a row that may since have gone.
_REF_COLUMNS = (
    "incident_no", "action_no", "drill_no", "talk_no", "doc_no", "hazard_no",
    "asset_no", "permit_no", "chemical_no", "meeting_no", "delivery_no",
)


def _is_ours(table: str) -> bool:
    return table.startswith("ehs_") and table != "ehs_change_log"


def _readable(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) > _MAX_VALUE:
        return text[:_MAX_VALUE] + "… (truncated)"
    return text


def _row_ref(obj: object) -> str | None:
    for column in _REF_COLUMNS:
        ref = getattr(obj, column, None)
        if ref:
            return str(ref)[:40]
    return None


def _entries_for(obj: object, action: str, actor: tuple, stamp: datetime) -> list:
    state = inspect(obj)
    table = obj.__tablename__
    row_id = getattr(obj, "id", None)
    if row_id is None:
        return []

    actor_id, actor_name = actor
    common = dict(
        table_name=table, row_id=str(row_id), row_ref=_row_ref(obj),
        changed_by=actor_id, changed_by_name=actor_name, changed_at=stamp,
    )

    # A creation is one row. The record itself is the account of what it was
    # created as, and a row per column would bury the changes that follow.
    if action in ("insert", "delete"):
        return [ChangeLogEntry(id=uuid.uuid4(), action=action, **common)]

    sensitive = table in _SENSITIVE_TABLES
    entries = []
    for attr in state.mapper.column_attrs:
        name = attr.key
        if name in _HOUSEKEEPING or name == "id":
            continue
        history = state.attrs[name].history
        if not history.has_changes():
            continue

        opaque = sensitive or name.endswith(_OPAQUE_SUFFIXES)
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        entries.append(ChangeLogEntry(
            id=uuid.uuid4(), action="update", field=name[:63],
            old_value=None if opaque else _readable(old),
            new_value=None if opaque else _readable(new),
            **common,
        ))
    return entries


def _actor_name_from(session: Session, actor_id) -> str | None:
    """The acting user's name, if the session already has it loaded.

    Free when it works and skipped when it does not: a query here would run
    inside the flush, on every write, to fill a column that is a convenience.
    The id is the record — `changed_by` is what the read endpoint joins on and
    what survives a rename.
    """
    if actor_id is None:
        return None
    for obj in session.identity_map.values():
        if getattr(obj, "__tablename__", None) == "users" and obj.id == actor_id:
            return getattr(obj, "full_name", None)
    return None


def _before_flush(session: Session, _flush_context, _instances) -> None:
    actor_id, actor_name = current_actor()
    actor = (actor_id, actor_name or _actor_name_from(session, actor_id))
    stamp = datetime.now(timezone.utc)
    pending: list = []

    for obj in session.new:
        if isinstance(obj, ChangeLogEntry) or not _is_ours(
                getattr(obj, "__tablename__", "")):
            continue
        pending.extend(_entries_for(obj, "insert", actor, stamp))

    for obj in session.dirty:
        if isinstance(obj, ChangeLogEntry) or not _is_ours(
                getattr(obj, "__tablename__", "")):
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        pending.extend(_entries_for(obj, "update", actor, stamp))

    for obj in session.deleted:
        if isinstance(obj, ChangeLogEntry) or not _is_ours(
                getattr(obj, "__tablename__", "")):
            continue
        pending.extend(_entries_for(obj, "delete", actor, stamp))

    for entry in pending:
        session.add(entry)


def install() -> None:
    """Called once at startup. Idempotent — registering twice would double
    every row, which is the kind of thing that looks like tampering."""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
