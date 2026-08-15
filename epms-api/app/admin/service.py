"""Generic CRUD + audit over registered entities."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.po_number import regenerate_and_cascade
from app.admin.recompute import recompute_header
from app.admin.registry import REGISTRY, EntitySpec
from app.admin.resolvers import get_resolver
from app.models.admin_audit_log import AdminAuditLog
from app.models.approval import ApprovalEvent
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.vendor import Vendor
from app.services import approval_client

# Entities whose documents run through the approval engine.
APPROVAL_STATE_ENTITIES = ("pr", "po", "pa", "agreement")

# Fallback engine doc_type when a document has no workflow history yet to read it from.
_DEFAULT_DOC_TYPE = {"pr": "pr", "po": "po", "pa": "pa", "agreement": "agr"}

# The engine only issues approve tasks for documents in these statuses
# (approval-api engine._resync_document); anything else is terminal to it.
_TASK_ISSUING_STATUSES = ("submitted", "in_review")


def _spec(entity: str) -> EntitySpec:
    spec = REGISTRY.get(entity)
    if spec is None:
        raise ValueError(f"Unknown entity '{entity}'")
    return spec


def _serialize(spec: EntitySpec, row) -> dict:
    out: dict = {"id": str(getattr(row, "id"))}
    for f in spec.schema.fields:
        if f.name == "source_requester_id":
            continue   # virtual — resolved on demand, not a column
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
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "t", "on")
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
        if qty in (None, "") or price in (None, ""):
            raise ValueError("Each line item needs a quantity and unit price")
        payload = {k: v for k, v in item.items() if k in editable and k != "line_total" and v != ""}
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
                      *, actor_id: uuid.UUID, actor_email: str,
                      regenerate_po_number: bool = False,
                      bearer_token: str | None = None) -> dict:
    spec = _spec(entity)
    if not spec.schema.allow_edit:
        raise ValueError(f"'{entity}' is delete-only and cannot be edited")
    row = await _load(db, spec, record_id)
    old_vendor_id = getattr(row, "vendor_id", None)
    patch = dict(patch)
    line_items = patch.pop("line_items", None)
    editable = spec.schema.editable_field_names()
    before = _serialize(spec, row)
    routing_requester_changed = False
    if entity == "pa":
        src_req = patch.pop("source_requester_id", None)
        if src_req not in (None, ""):
            po = (await db.execute(select(PurchaseOrder).where(PurchaseOrder.id == row.po_id))).scalar_one_or_none()
            if po is None or po.pr_id is None:
                raise ValueError("This PA has no linked source PR; source Requester cannot be changed")
            src_pr = (await db.execute(select(PurchaseRequest).where(PurchaseRequest.id == po.pr_id))).scalar_one()
            hit = await get_resolver("users").fetch_by_id(db, uuid.UUID(str(src_req)))
            if hit is None:
                raise ValueError(f"users reference '{src_req}' not found")
            src_pr.created_by = hit.id
            routing_requester_changed = True
    for key, value in patch.items():
        if key not in editable:
            raise ValueError(f"Field '{key}' is not editable")
        fspec = spec.schema.field_spec(key)
        if fspec is not None and fspec.type == "reference":
            await _apply_reference(db, spec, row, fspec, value)
            if entity == "pr" and fspec.name == "created_by":
                routing_requester_changed = True
        else:
            setattr(row, key, _coerce(spec.schema.field_type(key), value))
    if line_items is not None:
        await _apply_line_items(db, spec, row, line_items)

    cascade = None
    if entity == "po" and regenerate_po_number:
        new_vendor_id = getattr(row, "vendor_id", None)
        if new_vendor_id is not None and str(new_vendor_id) != str(old_vendor_id):
            vendor = (await db.execute(
                select(Vendor).where(Vendor.id == new_vendor_id))).scalar_one_or_none()
            if vendor is None:
                raise ValueError("New vendor not found for PO number regeneration")
            cascade = await regenerate_and_cascade(db, row, vendor.code)

    await db.flush()
    after = _serialize(spec, row)
    if line_items is not None:
        after["_line_items_count"] = len(line_items)
    after["_routing_requester_changed"] = routing_requester_changed
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit", system=spec.system,
        entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before=before,
        after=after,
        cascade_summary=cascade,
    ))
    await db.flush()
    return after


async def approval_doc_type(db: AsyncSession, entity: str, record_id: uuid.UUID) -> str:
    """The engine doc_type for this document.

    NOT simply the entity key. Direct PAs share the payment_applications table with
    PO-based PAs but run a different, shorter chain under doc_type "pa_dir" — asking
    the engine about "pa" would fetch the wrong workflow and stamp any new task with
    a document_type the OA inbox never queries. The workflow history is the
    authority on which chain a document is actually running; the entity default is
    only for documents that have none yet.
    """
    for model in (Task, ApprovalEvent):
        found = (await db.execute(
            select(model.document_type).where(model.document_id == record_id).limit(1)
        )).scalar_one_or_none()
        if found:
            return found
    return _DEFAULT_DOC_TYPE[entity]


async def edit_approval_state(db: AsyncSession, entity: str, record_id: uuid.UUID, patch: dict,
                              *, actor_id: uuid.UUID, actor_email: str,
                              bearer_token: str | None = None) -> dict:
    """Manually correct a document's live approval position.

    Setting `approval_step_idx` REBUILDS the approval task at that step (the caller
    completes the rebuild by calling the engine's resync after the commit — see
    `resync_doc_type` in the result). This used to only reassign tasks that already
    existed, which meant a document with no open approve task got a new step number,
    a detail page that rendered it, and still no approval button anywhere — the
    approval UI is gated on the tasks table, not on approval_step_idx. Every one of
    those ended in a hand-written script.

    Two guards exist because the engine fails SILENTLY in both cases (it returns
    None and the panel used to report success): a step past the end of the chain,
    and a document in a status the engine will not issue tasks for.

    Passing only `assigned_role` / `assigned_user_id` keeps the original behaviour:
    reassign the open approve tasks, touch nothing else.
    """
    spec = _spec(entity)
    if entity not in APPROVAL_STATE_ENTITIES:
        raise ValueError(f"'{entity}' has no approval state")
    row = await _load(db, spec, record_id)
    before = {"approval_step_idx": getattr(row, "approval_step_idx", None),
              "status": getattr(row, "status", None)}

    resync_doc_type: str | None = None
    closed_stale = 0
    new_idx = patch.get("approval_step_idx")
    if new_idx is not None:
        new_idx = int(new_idx)
        # Status first: it is the cheaper check and needs no round trip.
        status = getattr(row, "status", None)
        if status not in _TASK_ISSUING_STATUSES:
            raise ValueError(
                f"Approval tasks are only issued for documents in "
                f"{' or '.join(_TASK_ISSUING_STATUSES)} — this one is '{status}'. "
                "Set the status on the Edit tab first, then set the step."
            )
        resync_doc_type = await approval_doc_type(db, entity, record_id)
        steps = await approval_client.get_workflow_steps(
            resync_doc_type, str(record_id), bearer_token)
        if not 0 <= new_idx < len(steps):
            chain = ", ".join(f"{i}={s.get('label') or s.get('role')}"
                              for i, s in enumerate(steps))
            raise ValueError(
                f"Step {new_idx} is out of range for this document's "
                f"{len(steps)}-step workflow. Valid steps: {chain}."
            )
        row.approval_step_idx = new_idx
        # The engine derives the true step from any OPEN approve task in preference
        # to the stored index (engine._resync_document), so leaving a stale one open
        # makes the resync "realign" the admin's new step straight back to the old
        # one. Close them so the index is the only signal left — the same thing the
        # engine itself does before reissuing (_complete_tasks → _create_approve_task).
        # Only approve% tasks: place_order / create_pa / process_pa are legitimate
        # next-step work and must survive.
        stale = (await db.execute(select(Task).where(
            Task.document_id == record_id, Task.type.like("approve%"),
            Task.is_completed.is_(False)))).scalars().all()
        now = datetime.now(timezone.utc)
        for t in stale:
            t.is_completed = True
            t.completed_at = now
        closed_stale = len(stale)

    new_role = patch.get("assigned_role")
    new_user = patch.get("assigned_user_id")
    reassigned = 0
    if (new_role is not None or new_user is not None) and resync_doc_type is None:
        reassigned = await apply_task_override(db, record_id, new_role, new_user)

    await db.flush()
    after = {"approval_step_idx": getattr(row, "approval_step_idx", None),
             "reassigned_open_tasks": reassigned,
             "closed_stale_tasks": closed_stale,
             "resync_doc_type": resync_doc_type,
             "assigned_role": new_role, "assigned_user_id": new_user}
    db.add(AdminAuditLog(
        actor_id=actor_id, actor_email=actor_email, action="edit_approval_state",
        system=spec.system, entity=entity, record_id=record_id,
        record_number=str(getattr(row, spec.schema.number_field, None)),
        before=before, after=after))
    await db.flush()
    return after


async def count_open_approve_tasks(db: AsyncSession, record_id: uuid.UUID) -> int:
    """Positive evidence for the panel: an approval button exists iff this is > 0."""
    return int((await db.execute(
        select(func.count()).select_from(Task).where(
            Task.document_id == record_id, Task.type.like("approve%"),
            Task.is_completed.is_(False))
    )).scalar_one())


async def apply_task_override(db: AsyncSession, record_id: uuid.UUID,
                              role: str | None, user_id) -> int:
    """Force the open approve tasks onto a specific role / user.

    When a step change is in play this runs AFTER the engine has reissued the task,
    not before: the pre-resync tasks are the stale ones being closed, so overriding
    them there would write the admin's choice onto rows nobody will ever see.
    """
    open_tasks = (await db.execute(select(Task).where(
        Task.document_id == record_id, Task.type.like("approve%"),
        Task.is_completed.is_(False)))).scalars().all()
    for t in open_tasks:
        if role is not None:
            t.assigned_role = role
        if user_id is not None:
            t.assigned_user_id = uuid.UUID(str(user_id)) if user_id else None
    await db.flush()
    return len(open_tasks)


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
