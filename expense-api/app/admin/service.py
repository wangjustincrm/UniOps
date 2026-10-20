"""Generic CRUD + audit over registered OA entities."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ARRAY, JSON, Enum, String, cast, func, or_, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.registry import REGISTRY, EntitySpec
from app.models.admin_audit_log import AdminAuditLog


def _spec(entity: str) -> EntitySpec:
    spec = REGISTRY.get(entity)
    if spec is None:
        raise ValueError(f"Unknown entity '{entity}'")
    return spec


class UnknownSortKey(Exception):
    """?sort= named a column this entity's list does not offer -> HTTP 422."""


def _sort_expr(model, name: str):
    """ORDER BY expression for one list column, or None when it cannot sort.

    Only real mapped columns qualify — list_columns is free text in the
    registry, and this lookup is the SQL-injection boundary for ?sort=.
    Text sorts case-insensitively ("amazon" with "Amazon"); an enum by its
    label (a PG enum would otherwise order by declaration position);
    JSON/array columns are not sortable."""
    col = sa_inspect(model).columns.get(name)
    if col is None:
        return None
    attr = getattr(model, name)
    t = col.type
    if isinstance(t, (JSON, ARRAY)):
        return None
    if isinstance(t, Enum):            # Enum subclasses String: check it first
        return func.lower(cast(attr, String))
    if isinstance(t, String):          # Text is a String too
        return func.lower(func.nullif(func.trim(attr), ""))   # trimmed; blank = empty
    return attr


# entity -> {list column -> ORDER BY expression}. Built once from the registry,
# so the list page can only ever sort by a column it actually shows.
SORTABLE: dict[str, dict] = {
    key: {c: e for c in spec.schema.list_columns
          if (e := _sort_expr(spec.model, c)) is not None}
    for key, spec in REGISTRY.items()
}


def _serialize(spec: EntitySpec, row) -> dict:
    out: dict = {"id": str(getattr(row, "id"))}
    for f in spec.schema.fields:
        v = getattr(row, f.name, None)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        out[f.name] = v
    return out


def _coerce(field_type: str, value):
    if value is None:
        return None
    if field_type == "decimal":
        return Decimal(str(value))
    if field_type == "number":
        return int(value)
    if field_type == "bool":
        return bool(value)
    if field_type == "date":
        return date.fromisoformat(value) if isinstance(value, str) else value
    if field_type == "datetime":
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    return value


async def list_records(db: AsyncSession, entity: str, *, page: int, page_size: int,
                       search: str | None, sort: str | None = None,
                       dir: str = "asc") -> tuple[list[dict], int]:
    spec = _spec(entity)
    model = spec.model
    if sort is not None and sort not in SORTABLE[entity]:
        raise UnknownSortKey(f"'{entity}' cannot be sorted by {sort!r}")
    stmt = select(model)
    if search and spec.schema.search_fields:
        clauses = [getattr(model, f).ilike(f"%{search}%") for f in spec.schema.search_fields]
        stmt = stmt.where(or_(*clauses))
    total = int((await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one())
    if sort is None:
        col, _, direction = spec.schema.order_by.partition(" ")
        order = getattr(model, col)
        stmt = stmt.order_by(order.desc() if direction.lower() == "desc" else order.asc())
    else:
        expr = SORTABLE[entity][sort]
        expr = expr.asc() if dir == "asc" else expr.desc()
        # NULLs last both ways (PG DESC puts them first); id keeps pages stable.
        stmt = stmt.order_by(expr.nulls_last(), model.id.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(spec, r) for r in rows], total


async def get_record(db: AsyncSession, entity: str, record_id: uuid.UUID) -> dict | None:
    spec = _spec(entity)
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    return _serialize(spec, row) if row else None


async def _load(db: AsyncSession, spec: EntitySpec, record_id: uuid.UUID):
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    if row is None:
        raise ValueError("Record not found")
    return row


async def edit_record(db: AsyncSession, entity: str, record_id: uuid.UUID, patch: dict,
                      *, actor_id: uuid.UUID, actor_email: str) -> dict:
    spec = _spec(entity)
    row = await _load(db, spec, record_id)
    editable = spec.schema.editable_field_names()
    before = _serialize(spec, row)
    for key, value in patch.items():
        if key not in editable:
            raise ValueError(f"Field '{key}' is not editable")
        setattr(row, key, _coerce(spec.schema.field_type(key), value))
    await db.flush()
    after = _serialize(spec, row)
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit", system=spec.system,
        entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before={k: before[k] for k in patch if k in before},
        after={k: after[k] for k in patch if k in after},
    ))
    await db.flush()
    return after


async def delete_preview(db: AsyncSession, entity: str, record_id: uuid.UUID) -> dict[str, int]:
    spec = _spec(entity)
    row = await _load(db, spec, record_id)
    return await spec.cascade_preview(db, row)


async def delete_record(db: AsyncSession, entity: str, record_id: uuid.UUID,
                        *, actor_id: uuid.UUID, actor_email: str) -> dict[str, int]:
    spec = _spec(entity)
    row = await _load(db, spec, record_id)
    snapshot = _serialize(spec, row)
    number = str(getattr(row, spec.schema.number_field, None))
    summary = await spec.cascade_delete(db, row)
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="delete", system=spec.system,
        entity=entity, record_id=record_id, record_number=number,
        before=snapshot, after=None, cascade_summary=summary,
    ))
    await db.flush()
    return summary


async def bulk_delete(db: AsyncSession, entity: str, record_ids: list[uuid.UUID],
                      *, actor_id: uuid.UUID, actor_email: str) -> dict[str, int]:
    total: dict[str, int] = {}
    for rid in record_ids:
        summary = await delete_record(db, entity, rid, actor_id=actor_id, actor_email=actor_email)
        for k, v in summary.items():
            total[k] = total.get(k, 0) + v
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="bulk_delete", system=_spec(entity).system,
        entity=entity, record_id=record_ids[0] if record_ids else uuid.uuid4(),
        record_number=f"{len(record_ids)} records", before=None, after=None, cascade_summary=total,
    ))
    await db.flush()
    return total
