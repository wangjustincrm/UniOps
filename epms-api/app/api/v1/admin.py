"""Cross-system data-maintenance admin endpoints (Phase 1: EPMS)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel

from app.admin import service
from app.admin.registry import REGISTRY
from app.admin.resolvers import get_resolver, resolver_sources
from app.core.deps import BearerToken, SessionDep, require_permission
from app.services import approval_client

router = APIRouter(prefix="/admin", tags=["data-maintenance"])

AdminUser = Annotated[dict, Depends(require_permission("data_maintenance"))]


class ListResponse(BaseModel):
    items: list[dict]
    total: int


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID]


def _actor(user: dict) -> tuple[uuid.UUID, str]:
    return uuid.UUID(user["sub"]), user.get("email", "")


@router.get("/entities")
async def list_entities(user: AdminUser):
    return [spec.schema.to_dict() | {"system": spec.system} for spec in REGISTRY.values()]


@router.get("/lookup/{source}")
async def lookup(source: str, db: SessionDep, user: AdminUser,
                 q: str = Query(""), limit: int = Query(20, le=50)):
    if source not in resolver_sources():
        raise HTTPException(404, f"Unknown reference source '{source}'")
    hits = await get_resolver(source).search(db, q, limit)
    return [{"id": str(h.id), "label": h.label} for h in hits]


@router.get("/{entity}", response_model=ListResponse)
async def list_records(entity: str, db: SessionDep, user: AdminUser,
                       page: int = Query(1, ge=1), page_size: int = Query(20, le=200),
                       search: str | None = Query(None)):
    try:
        items, total = await service.list_records(db, entity, page=page, page_size=page_size, search=search)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return ListResponse(items=items, total=total)


@router.get("/{entity}/{record_id}")
async def get_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser):
    try:
        rec = await service.get_record(db, entity, record_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if rec is None:
        raise HTTPException(404, "Record not found")
    return rec


@router.patch("/{entity}/{record_id}")
async def edit_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                      token: BearerToken,
                      patch: dict = Body(...),
                      regenerate_po_number: int = Query(0)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_record(db, entity, record_id, patch, actor_id=actor_id,
                                            actor_email=email,
                                            regenerate_po_number=bool(regenerate_po_number),
                                            bearer_token=token)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    # Post-commit, best-effort: approval-api reads the shared DB, so the resync
    # must run after the transaction lands. A failure here must never fail the
    # edit itself — surface it as a warning field instead.
    if result.pop("_routing_requester_changed", False):
        try:
            await approval_client.resync_document(entity, str(record_id), bearer_token=token)
            result["routing_resync"] = "ok"
        except Exception as e:
            result["routing_resync"] = f"failed: {e}"
    return result


@router.get("/{entity}/{record_id}/workflow-steps")
async def workflow_steps(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                         token: BearerToken):
    """The document's effective approval chain, so the panel can offer the real steps
    instead of a free-text number box — typing a step past the end of the chain is
    exactly how a document ends up with a step the engine silently refuses to act on."""
    if entity not in service.APPROVAL_STATE_ENTITIES:
        raise HTTPException(400, f"'{entity}' has no approval state")
    doc_type = await service.approval_doc_type(db, entity, record_id)
    try:
        steps = await approval_client.get_workflow_steps(doc_type, str(record_id),
                                                         bearer_token=token)
    except Exception as e:
        raise HTTPException(502, f"Approval Engine: {e}")
    return {"doc_type": doc_type, "steps": steps,
            "open_approve_tasks": await service.count_open_approve_tasks(db, record_id)}


@router.patch("/{entity}/{record_id}/approval-state")
async def edit_approval_state(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                              token: BearerToken,
                              patch: dict = Body(...)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_approval_state(db, entity, record_id, patch,
                                                   actor_id=actor_id, actor_email=email,
                                                   bearer_token=token)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    except (RuntimeError, LookupError) as e:
        # Validating the step needs the engine's workflow for this document. If the
        # engine is unreachable this must read as "I could not check", not as a
        # generic 500 — nothing has been changed at this point.
        await db.rollback()
        raise HTTPException(502, f"Approval Engine: {e}")

    # A step change is only half done here: the row now says step N and its stale
    # approve tasks are closed, but nothing has ISSUED the task at step N — and the
    # approval UI is gated on tasks, not on approval_step_idx. The engine does that,
    # and it reads the shared DB, so it must run after the commit lands.
    doc_type = result.pop("resync_doc_type", None)
    if doc_type:
        try:
            engine = await approval_client.resync_document(doc_type, str(record_id),
                                                           bearer_token=token)
            summary = (engine or {}).get("resynced") or {}
            actions = summary.get("actions") or []
            result["routing_resync"] = "ok"
            result["resync_actions"] = actions
            result["final_step"] = summary.get("final_step")
            # The engine reports an unresolvable assignee as a WARN string inside
            # `actions` and creates no task. That is a silent failure unless someone
            # looks at it, so flag it rather than letting the panel print "ok".
            result["resync_warning"] = any(str(a).startswith("WARN") for a in actions)
        except Exception as e:
            # resync-document is system_admin-only and cross-service. The step change
            # is already committed — report the gap instead of pretending it worked.
            result["routing_resync"] = f"failed: {e}"
            result["resync_actions"] = []
            result["resync_warning"] = True

        # Any explicit role/user override lands on the task the engine just issued.
        role, uid = result.get("assigned_role"), result.get("assigned_user_id")
        if role is not None or uid is not None:
            result["reassigned_open_tasks"] = await service.apply_task_override(
                db, record_id, role, uid)
            await db.commit()

    result["open_approve_tasks"] = await service.count_open_approve_tasks(db, record_id)
    return result


@router.delete("/{entity}/{record_id}")
async def delete_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminUser,
                        preview: int = Query(0)):
    actor_id, email = _actor(user)
    try:
        if preview:
            summary = await service.delete_preview(db, entity, record_id)
            return {"preview": True, "cascade": summary}
        summary = await service.delete_record(db, entity, record_id, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"preview": False, "cascade": summary}
    except ValueError as e:
        await db.rollback()
        raise HTTPException(404, str(e))


@router.post("/{entity}/bulk-delete")
async def bulk_delete(entity: str, db: SessionDep, user: AdminUser, body: BulkDeleteRequest):
    actor_id, email = _actor(user)
    try:
        summary = await service.bulk_delete(db, entity, body.ids, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"deleted": len(body.ids), "cascade": summary}
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
