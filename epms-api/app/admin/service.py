"""Generic CRUD + audit over registered entities."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.recompute import recompute_header
from app.admin.registry import REGISTRY, EntitySpec
from app.admin.resolvers import get_resolver
from app.models.admin_audit_log import AdminAuditLog
from app.models.task import Task


def _spec(entity: str) -> EntitySpec:
    spec = REGISTRY.get(entity)
    if spec is None:
        raise ValueError(f"Unknown entity '{entity}'")
    return spec


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
                       search: str | None) -> tuple[list[dict], int]:
    spec = _spec(entity)
    model = spec.model
    stmt = select(model)
    if search and spec.schema.search_fields:
        clauses = [getattr(model, f).ilike(f"%{search}%") for f in spec.schema.search_fields]
        stmt = stmt.where(or_(*clauses))
    total = int((await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )).scalar_one())
    col, _, direction = spec.schema.order_by.partition(" ")
    order = getattr(model, col)
    stmt = stmt.order_by(order.desc() if direction.lower() == "desc" else order.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize(spec, r) for r in rows], total


def _serialize_child(child, li) -> dict:
    out = {"id": str(getattr(li, "id"))}
    for f in child.fields:
        v = getattr(li, f.name, None)
        if isinstance(v, (datetime, date)):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, uuid.UUID):
            v = str(v)
        out[f.name] = v
    return out


async def get_record(db: AsyncSession, entity: str, record_id: uuid.UUID) -> dict | None:
    spec = _spec(entity)
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    if row is None:
        return None
    rec = _serialize(spec, row)
    child = spec.schema.child
    if child is not None:
        lis = (await db.execute(
            select(child.model).where(getattr(child.model, child.fk_field) == row.id)
            .order_by(child.model.sort_order.asc()))).scalars().all()
        rec["line_items"] = [_serialize_child(child, li) for li in lis]
    return rec


async def _load(db: AsyncSession, spec: EntitySpec, record_id: uuid.UUID):
    row = (await db.execute(select(spec.model).where(spec.model.id == record_id))).scalar_one_or_none()
    if row is None:
        raise ValueError("Record not found")
    return row


async def _apply_reference(db, spec, row, field, value):
    """Set an FK reference field + sync its denormalized name column. Returns the
    resolved label (for audit) or raises ValueError if the id is unknown."""
    if value in (None, ""):
        raise ValueError(f"Field '{field.name}' is a required reference and cannot be cleared")
    rid = uuid.UUID(str(value))
    hit = await get_resolver(field.ref_source).fetch_by_id(db, rid)
    if hit is None:
        raise ValueError(f"{field.ref_source} reference '{rid}' not found")
    setattr(row, field.name, rid)
    if field.ref_name_field:
        setattr(row, field.ref_name_field, hit.label)
    return hit.label


def _line_total(qty, unit_price) -> Decimal:
    return (Decimal(str(qty)) * Decimal(str(unit_price))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _apply_line_items(db, spec, row, items: list[dict]):
    """Diff the submitted line-item array against DB rows: update (has id), insert
    (no id), delete (existing id absent). line_total is recomputed server-side.
    Then recompute the header. Returns the list of resulting line dicts (for recompute)."""
    child = spec.schema.child
    if child is None:
        raise ValueError(f"'{spec.schema.key}' has no line items")
    Model = child.model
    editable = child.editable_field_names()
    existing = {li.id: li for li in (await db.execute(
        select(Model).where(getattr(Model, child.fk_field) == row.id))).scalars().all()}

    seen: set = set()
    for idx, item in enumerate(items):
        raw_id = item.get("id")
        qty = item.get("qty"); price = item.get("unit_price")
        if qty is None or price is None:
            raise ValueError("Each line item needs qty and unit_price")
        payload = {k: v for k, v in item.items() if k in editable and k != "line_total"}
        payload.setdefault("sort_order", idx)
        lt = _line_total(qty, price)
        if raw_id:
            li = existing.get(uuid.UUID(str(raw_id)))
            if li is None:
                raise ValueError(f"Line item '{raw_id}' does not belong to this record")
            for k, v in payload.items():
                setattr(li, k, _coerce(child.field_type(k), v))
            li.line_total = lt
            seen.add(li.id)
        else:
            coerced = {k: _coerce(child.field_type(k), v) for k, v in payload.items()}
            db.add(Model(**{child.fk_field: row.id}, line_total=lt, **coerced))

    # delete existing rows the payload dropped
    for lid, li in existing.items():
        if lid not in seen:
            await db.delete(li)

    await db.flush()
    lines = (await db.execute(
        select(Model).where(getattr(Model, child.fk_field) == row.id))).scalars().all()
    line_dicts = [{"line_total": l.line_total} for l in lines]
    for hk, hv in recompute_header(spec.schema.key, row, line_dicts).items():
        setattr(row, hk, hv)


async def edit_record(db: AsyncSession, entity: str, record_id: uuid.UUID, patch: dict,
                      *, actor_id: uuid.UUID, actor_email: str) -> dict:
    spec = _spec(entity)
    if not spec.schema.allow_edit:
        raise ValueError(f"'{entity}' is delete-only and cannot be edited")
    row = await _load(db, spec, record_id)
    patch = dict(patch)
    line_items = patch.pop("line_items", None)
    editable = spec.schema.editable_field_names()
    before = _serialize(spec, row)
    for key, value in patch.items():
        if key not in editable:
            raise ValueError(f"Field '{key}' is not editable")
        fspec = spec.schema.field_spec(key)
        if fspec is not None and fspec.type == "reference":
            await _apply_reference(db, spec, row, fspec, value)
        else:
            setattr(row, key, _coerce(spec.schema.field_type(key), value))
    if line_items is not None:
        await _apply_line_items(db, spec, row, line_items)
    await db.flush()
    after = _serialize(spec, row)
    if line_items is not None:
        after["_line_items_count"] = len(line_items)
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit", system=spec.system,
        entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before=before,
        after=after,
    ))
    await db.flush()
    return after


async def edit_approval_state(db: AsyncSession, entity: str, record_id: uuid.UUID, patch: dict,
                              *, actor_id: uuid.UUID, actor_email: str) -> dict:
    """Manually correct a document's live approval position: its approval_step_idx
    and the assignment of its OPEN approve tasks. Does NOT re-run the engine, send
    notifications, or touch completed tasks / approval_events."""
    spec = _spec(entity)
    if entity not in ("pr", "po", "pa"):
        raise ValueError(f"'{entity}' has no approval state")
    row = await _load(db, spec, record_id)
    before = {"approval_step_idx": getattr(row, "approval_step_idx", None)}

    new_idx = patch.get("approval_step_idx")
    if new_idx is not None:
        row.approval_step_idx = int(new_idx)

    new_role = patch.get("assigned_role")
    new_user = patch.get("assigned_user_id")
    reassigned = 0
    if new_role is not None or new_user is not None:
        open_tasks = (await db.execute(select(Task).where(
            Task.document_id == record_id, Task.type.like("approve%"),
            Task.is_completed.is_(False)))).scalars().all()
        for t in open_tasks:
            if new_role is not None:
                t.assigned_role = new_role
            if new_user is not None:
                t.assigned_user_id = uuid.UUID(str(new_user)) if new_user else None
            reassigned += 1

    await db.flush()
    after = {"approval_step_idx": getattr(row, "approval_step_idx", None),
             "reassigned_open_tasks": reassigned,
             "assigned_role": new_role, "assigned_user_id": new_user}
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit_approval_state",
        system=spec.system, entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before=before, after=after))
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
