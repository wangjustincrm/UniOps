"""Department routing rules + gm/opm backups — Phase 3 admin surface.

Owns `approval_dept_routing` / `approval_backups` (app/models/routing.py).
Department identity (id/code/name/is_active) is epms/identity-owned; this
router only reads it via raw SQL against the shared `departments` table —
no ORM model here, matching the rest of this service's cross-service reads
(see app/crud/workflow.py's `_post_holders`).
"""
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud.engine import reroute_stranded_optional_steps
from app.db.base import get_db

router = APIRouter(prefix="/routing", tags=["routing"])

_VALID_POSTS = ("gm", "opm")
_BACKUP_ROLES = ("gm", "opm")


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

    return {"departments": departments, "backups": backups}


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

    await db.commit()
    return await _load_routing(db)


@router.post("/reroute-inflight")
async def reroute_inflight(db: AsyncSession = Depends(get_db), user: CurrentUser = ...):
    """Re-route in-flight documents stranded on an optional (Director/Supervisor)
    step that the CURRENT config now skips — advance each to its next real
    approver (or approve it). Run after changing Director/Supervisor routing or a
    user's role so already-submitted documents don't stay stuck (their old
    optional-step assignee can no longer approve → 409). Idempotent: only touches
    documents whose current step is genuinely skippable now. system_admin only.
    """
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    results = await reroute_stranded_optional_steps(db)
    await db.commit()
    return {"rerouted": len(results), "documents": results}
