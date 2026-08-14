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

# Granting the EPMS "Vendor Master" permission must also grant the mdm-layer
# key that actually gates vendor create/update, so one Access Control checkbox
# works end to end. See
# docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md
COUPLED_PERMISSIONS: dict[str, str] = {"vendor_master": "mdm.vendor.write"}


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


async def _apply_grant(db, role: str, key: str, val: bool, actor: uuid.UUID) -> None:
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
            await _apply_grant(db, role, key, val, actor)
            # mdm.vendor.write is driven SOLELY by vendor_master here (its own
            # matrix row is hidden in the UI), so mirroring val symmetrically is
            # safe: a revoke also clears any independent grant, and a delta never
            # carries both keys with conflicting values. This single-switch model
            # is what makes the revoke blast-radius and dict-ordering edges benign.
            coupled = COUPLED_PERMISSIONS.get(key)
            # Guard on perm_keys: only mirror when the coupled key is a
            # registered permission_def, else the FK insert would blow up an
            # env where phase-2 keys were never seeded.
            if coupled and coupled in perm_keys:
                await _apply_grant(db, role, coupled, val, actor)
    return _matrix(*await _load(db))


@router.get("/authz/defs")
async def get_defs(db: SessionDep, _: CurrentUserPayload) -> dict:
    roles, perms, _granted, locks = await _load(db)
    locked_for: dict[str, list[str]] = {}
    for role, key in locks:
        locked_for.setdefault(key, []).append(role)
    return {
        "roles": [{"code": r.code, "label": r.label, "sort": r.sort,
                   "is_active": r.is_active,
                   "assignable_as_primary": r.assignable_as_primary} for r in roles],
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


@router.get("/authz/user-roles")
async def get_user_roles(db: SessionDep, user: CurrentUserPayload) -> dict:
    """Return every user's ADDITIONAL roles (system_admin only).

    Primary role lives on users.role (already returned by the epms /users
    list); this only covers the user_roles side table. Used to prefill the
    Access Control "User Roles" tab so saving a row never silently wipes
    additional roles the admin didn't intend to touch.
    """
    _require_admin(user)
    rows = (await db.execute(select(UserRole))).scalars().all()
    out: dict[str, list[str]] = {}
    for ur in rows:
        out.setdefault(str(ur.user_id), []).append(ur.role_code)
    for uid in out:
        out[uid] = sorted(out[uid])
    return {"user_roles": out}


class UserRolesPut(BaseModel):
    primary: str
    additional: list[str] = []


_POST_ROLES = frozenset({"gm", "opm", "vendor_manager", "finance_manager", "procurement_manager"})


async def _post_conflict(db, user_id: uuid.UUID, wanted: set[str]) -> dict | None:
    """A post role may be held by exactly one user — as primary OR additional."""
    posts = wanted & _POST_ROLES
    if not posts:
        return None
    holder = (await db.execute(select(User.id, User.role).where(
        User.role.in_(posts), User.id != user_id))).first()
    if holder is not None:
        return {"role": holder[1], "held_by": str(holder[0])}
    row = (await db.execute(select(UserRole.role_code, UserRole.user_id).where(
        UserRole.role_code.in_(posts), UserRole.user_id != user_id))).first()
    if row is not None:
        return {"role": row[0], "held_by": str(row[1])}
    return None


@router.put("/authz/users/{user_id}/roles", status_code=204)
async def put_user_roles(user_id: uuid.UUID, body: UserRolesPut,
                         db: SessionDep, user: CurrentUserPayload) -> None:
    _require_admin(user)
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    defs = {r.code: r for r in (await db.execute(select(RoleDef))).scalars().all()}
    codes = set(defs)
    if body.primary not in codes:
        raise HTTPException(status_code=422, detail=f"Unknown role '{body.primary}'")
    # ADDITIONAL-ONLY roles (erp_pa_officer / payment_officer) are granted through
    # user_roles and must never become users.role — payment_officer as a primary
    # role wins finance-api's PRIMARY-role payment short-circuit while bypassing
    # the whole additional-role model. The frontends filter their dropdowns on the
    # same flag from /authz/defs; this is the check that a direct API call hits.
    if not defs[body.primary].assignable_as_primary:
        raise HTTPException(
            status_code=422,
            detail=f"Role '{body.primary}' is an additional-only role and cannot be "
                   f"a primary role — grant it under Additional Roles instead")
    bad = [c for c in body.additional if c not in codes]
    if bad:
        raise HTTPException(status_code=422, detail=f"Unknown roles {bad}")
    conflict = await _post_conflict(db, user_id, {body.primary, *body.additional})
    if conflict is not None:
        raise HTTPException(status_code=409, detail={"conflict": conflict})
    u.role = body.primary
    await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for code in set(body.additional) - {body.primary}:
        db.add(UserRole(user_id=user_id, role_code=code))
