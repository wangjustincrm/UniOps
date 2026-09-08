"""Department routing rules + gm/opm backups — Phase 3 admin surface.

Owns `approval_dept_routing` / `approval_backups` (app/models/routing.py).
Department identity (id/code/name/is_active) is epms/identity-owned; this
router only reads it via raw SQL against the shared `departments` table —
no ORM model here, matching the rest of this service's cross-service reads
(see app/crud/workflow.py's `_post_holders`).
"""
import logging
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud.engine import _resync_document, resync_inflight_approvals
from app.db.base import get_db
from app.models.routing import CROSS_DEPT_GM_OR_OPM

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/routing", tags=["routing"])

_VALID_POSTS = ("gm", "opm")
_BACKUP_ROLES = ("gm", "opm")


async def _resync_after_config_change(db: AsyncSession) -> dict:
    """Hand in-flight approvals to whoever the NEW config resolves to.

    Approval tasks for department-scoped roles are PINNED to a specific user at
    creation time (engine._USER_SPECIFIC_ROLES), so a config change alone leaves
    them pointing at the previous holder — the new one sees nothing in their Task
    Inbox and the old one can no longer act. Best-effort: the config write has
    already committed, so a failing resync is REPORTED, never raised.
    """
    try:
        result = await resync_inflight_approvals(db)
        await db.commit()
        return {"resynced": len(result["resynced"]), "errors": len(result["errors"])}
    except Exception as exc:
        await db.rollback()
        _log.warning("routing config saved but in-flight resync failed: %s: %s",
                     type(exc).__name__, exc, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _load_routing(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT d.id::text AS dept_id, d.code AS dept_code, d.name AS dept_name, "
        "       COALESCE(r.gm_or_opm, 'gm') AS gm_or_opm, "
        "       r.director_user_id::text AS director_user_id, "
        "       COALESCE(r.supervisor_enabled, false) AS supervisor_enabled "
        "FROM departments d "
        "LEFT JOIN approval_dept_routing r ON r.dept_id = d.id "
        "WHERE d.is_active "
        "ORDER BY d.code"
    ))).mappings().all()
    departments = [dict(r) for r in rows]

    backup_rows = (await db.execute(sa.text(
        "SELECT role_code, backup_user_id::text FROM approval_backups"))).all()
    backups: dict[str, str | None] = {code: None for code in _BACKUP_ROLES}
    for role_code, backup_user_id in backup_rows:
        if role_code in backups:
            backups[role_code] = backup_user_id

    # Engine-wide settings — not per department. Today: which post approves a
    # payment application that spans several departments, where the per-department
    # mapping above has no answer (one of them may say GM and another OPM).
    cross_dept = (await db.execute(sa.text(
        "SELECT value FROM approval_settings WHERE key = :k"),
        {"k": CROSS_DEPT_GM_OR_OPM})).scalar_one_or_none() or "gm"

    return {
        "departments": departments,
        "backups": backups,
        "cross_department_gm_or_opm": cross_dept,
    }


@router.get("")
async def get_routing(db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    return await _load_routing(db)


@router.put("")
async def put_routing(body: dict, db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")

    departments = body.get("departments") or []
    backups = body.get("backups") or {}
    actor_id = user.get("sub")

    dept_ids = [str(d.get("dept_id")) for d in departments]
    if dept_ids:
        active_rows = (await db.execute(sa.text(
            "SELECT id::text FROM departments WHERE is_active AND id = ANY(:ids)"),
            {"ids": dept_ids})).scalars().all()
        active_ids = set(active_rows)
    else:
        active_ids = set()

    for d in departments:
        dept_id = str(d.get("dept_id"))
        gm_or_opm = d.get("gm_or_opm")
        if dept_id not in active_ids:
            raise HTTPException(status_code=422, detail=f"Unknown or inactive dept_id: {dept_id}")
        if gm_or_opm not in _VALID_POSTS:
            raise HTTPException(
                status_code=422,
                detail=f"gm_or_opm must be one of {_VALID_POSTS}, got: {gm_or_opm}")

    for role_code in backups:
        if role_code not in _BACKUP_ROLES:
            raise HTTPException(status_code=422, detail=f"Unknown backup role: {role_code}")

    # Omitted = leave it alone. Sent = must be a real post: an invalid value here
    # would otherwise silently fall back to GM inside the engine, and a setting
    # that quietly ignores what the admin chose is worse than one that refuses.
    cross_dept = body.get("cross_department_gm_or_opm")
    if cross_dept is not None and cross_dept not in _VALID_POSTS:
        raise HTTPException(
            status_code=422,
            detail=f"cross_department_gm_or_opm must be one of {_VALID_POSTS}, got: {cross_dept}")

    for d in departments:
        await db.execute(sa.text(
            "INSERT INTO approval_dept_routing "
            "  (dept_id, gm_or_opm, director_user_id, supervisor_enabled, updated_by, updated_at) "
            "VALUES (:dept_id, :gm_or_opm, :director_user_id, :supervisor_enabled, :updated_by, now()) "
            "ON CONFLICT (dept_id) DO UPDATE SET "
            "  gm_or_opm = EXCLUDED.gm_or_opm, "
            "  director_user_id = EXCLUDED.director_user_id, "
            "  supervisor_enabled = EXCLUDED.supervisor_enabled, "
            "  updated_by = EXCLUDED.updated_by, "
            "  updated_at = now()"
        ), {
            "dept_id": str(d.get("dept_id")),
            "gm_or_opm": d.get("gm_or_opm"),
            "director_user_id": d.get("director_user_id"),
            "supervisor_enabled": bool(d.get("supervisor_enabled")),
            "updated_by": actor_id,
        })

    for role_code, backup_user_id in backups.items():
        if backup_user_id is None:
            await db.execute(sa.text(
                "DELETE FROM approval_backups WHERE role_code = :role_code"),
                {"role_code": role_code})
        else:
            await db.execute(sa.text(
                "INSERT INTO approval_backups (role_code, backup_user_id, updated_by, updated_at) "
                "VALUES (:role_code, :backup_user_id, :updated_by, now()) "
                "ON CONFLICT (role_code) DO UPDATE SET "
                "  backup_user_id = EXCLUDED.backup_user_id, "
                "  updated_by = EXCLUDED.updated_by, "
                "  updated_at = now()"
            ), {
                "role_code": role_code,
                "backup_user_id": backup_user_id,
                "updated_by": actor_id,
            })

    if cross_dept is not None:
        await db.execute(sa.text(
            "INSERT INTO approval_settings (key, value, updated_by, updated_at) "
            "VALUES (:k, :v, :updated_by, now()) "
            "ON CONFLICT (key) DO UPDATE SET "
            "  value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()"
        ), {"k": CROSS_DEPT_GM_OR_OPM, "v": cross_dept, "updated_by": actor_id})

    await db.commit()

    routing_resync = await _resync_after_config_change(db)
    return {**await _load_routing(db), "routing_resync": routing_resync}


@router.post("/resync-inflight")
async def resync_inflight(db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    """Realign in-flight documents to the CURRENT routing config. Run after
    changing dept gm↔opm mapping, Director/Supervisor routing, who holds a role,
    or a workflow's step list — so already-submitted documents don't stay stuck
    with a wrong approval_step_idx (→ 409 / "no permission") or a task assigned to
    the old approver. Realigns each doc's step to its open task's real step, skips
    now-unconfigured optional steps, and reassigns drifted approvers. Idempotent:
    only touches documents that are actually out of sync. system_admin only.
    """
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    result = await resync_inflight_approvals(db)
    await db.commit()
    # `resynced` / `errors` are the per-document detail lists — the ** spread of
    # `result` always overwrote the counts that used to be computed here, so
    # callers have only ever seen the lists. Say so instead of pretending.
    return result


@router.post("/resync-document")
async def resync_document(body: dict, db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    """Realign ONE in-flight document to current routing config (single-doc variant of
    resync-inflight). Used after an admin corrects a document's Requester so the
    department-derived approvers follow. system_admin only."""
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    doc_type = body.get("doc_type")
    doc_id = body.get("doc_id")
    if not doc_type or not doc_id:
        raise HTTPException(status_code=422, detail="doc_type and doc_id required")
    try:
        summary = await _resync_document(db, doc_type, uuid.UUID(str(doc_id)))
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}")
    await db.commit()
    return {"resynced": summary}
