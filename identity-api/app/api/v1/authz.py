"""Authz hub — matrix / defs / user roles / my effective permissions."""
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, text

from app.core.deps import CurrentUserPayload, SessionDep
from app.models.authz import (PermissionDef, RoleDef, RolePermission,
                              RolePermissionLock, UserRole)
from app.models.user import User

router = APIRouter(tags=["authz"])


def _require_admin(user: dict) -> uuid.UUID:
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    return uuid.UUID(user["sub"])


async def _load(db):
    roles = (await db.execute(select(RoleDef).order_by(RoleDef.sort))).scalars().all()
    perms = (await db.execute(select(PermissionDef).order_by(PermissionDef.sort))).scalars().all()
    granted = {(r.role_code, r.permission_key)
               for r in (await db.execute(select(RolePermission))).scalars().all()}
    locks = {(l.role_code, l.permission_key)
             for l in (await db.execute(select(RolePermissionLock))).scalars().all()}
    return roles, perms, granted, locks


def _matrix(roles, perms, granted, locks) -> dict:
    return {r.code: {p.key: ((r.code, p.key) in granted or (r.code, p.key) in locks)
                     for p in perms}
            for r in roles}


@router.get("/authz/matrix")
async def get_matrix(db: SessionDep, _: CurrentUserPayload) -> dict:
    return _matrix(*await _load(db))


class MatrixPatch(BaseModel):
    changes: dict[str, dict[str, bool]]


@router.patch("/authz/matrix")
async def patch_matrix(body: MatrixPatch, db: SessionDep, user: CurrentUserPayload) -> dict:
    actor = _require_admin(user)
    roles, perms, granted, locks = await _load(db)
    role_codes = {r.code for r in roles}
    perm_keys = {p.key for p in perms}
    hit_locks = []
    for role, kv in body.changes.items():
        if role not in role_codes:
            raise HTTPException(status_code=422, detail=f"Unknown role '{role}'")
        for key, val in kv.items():
            if key not in perm_keys:
                raise HTTPException(status_code=422, detail=f"Unknown permission '{key}'")
            if (role, key) in locks and val is False:
                hit_locks.append({"role": role, "key": key})
    if hit_locks:
        raise HTTPException(status_code=409, detail={"locked": hit_locks})
    for role, kv in body.changes.items():
        for key, val in kv.items():
            if val:
                await db.execute(text(
                    "INSERT INTO role_permissions(role_code,permission_key,updated_by) "
                    "VALUES (:r,:k,:u) ON CONFLICT (role_code,permission_key) "
                    "DO UPDATE SET updated_by=:u, updated_at=now()"),
                    {"r": role, "k": key, "u": str(actor)})
            else:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role_code == role,
                    RolePermission.permission_key == key))
    return _matrix(*await _load(db))


@router.get("/authz/defs")
async def get_defs(db: SessionDep, _: CurrentUserPayload) -> dict:
    roles, perms, _granted, locks = await _load(db)
    locked_for: dict[str, list[str]] = {}
    for role, key in locks:
        locked_for.setdefault(key, []).append(role)
    return {
        "roles": [{"code": r.code, "label": r.label, "sort": r.sort,
                   "is_active": r.is_active} for r in roles],
        "permissions": [{"key": p.key, "module": p.module, "label": p.label,
                         "sort": p.sort, "locked_for": sorted(locked_for.get(p.key, []))}
                        for p in perms],
    }


@router.get("/me/permissions")
async def my_permissions(db: SessionDep, user: CurrentUserPayload) -> dict:
    uid = uuid.UUID(user["sub"])
    u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    active = {r.code for r in (await db.execute(
        select(RoleDef).where(RoleDef.is_active.is_(True)))).scalars().all()}
    additional = [ur.role_code for ur in (await db.execute(
        select(UserRole).where(UserRole.user_id == uid))).scalars().all()
        if ur.role_code in active]
    my_roles = ([u.role] if u.role in active else []) + sorted(additional)
    roles, perms, granted, locks = await _load(db)
    result = {p.key: any((rc, p.key) in granted or (rc, p.key) in locks
                         for rc in my_roles) for p in perms}
    return {"permissions": result, "roles": ([u.role] + sorted(additional))}


class UserRolesPut(BaseModel):
    primary: str
    additional: list[str] = []


@router.put("/authz/users/{user_id}/roles", status_code=204)
async def put_user_roles(user_id: uuid.UUID, body: UserRolesPut,
                         db: SessionDep, user: CurrentUserPayload) -> None:
    _require_admin(user)
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    codes = {r.code for r in (await db.execute(select(RoleDef))).scalars().all()}
    if body.primary not in codes:
        raise HTTPException(status_code=422, detail=f"Unknown role '{body.primary}'")
    bad = [c for c in body.additional if c not in codes]
    if bad:
        raise HTTPException(status_code=422, detail=f"Unknown roles {bad}")
    u.role = body.primary
    await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for code in set(body.additional) - {body.primary}:
        db.add(UserRole(user_id=user_id, role_code=code))
