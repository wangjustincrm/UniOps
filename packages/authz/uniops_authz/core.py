"""The gate. Reads identity's matrix directly — same physical database."""
import uuid
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """The user's PRIMARY role plus every ADDITIONAL role from identity's
    user_roles. Both sources count: the primary role is a real role, and
    consulting only user_roles would silently lose primary-role holders."""
    codes: set[str] = {base_role} if base_role else set()
    rows = (await db.execute(text(
        "SELECT ur.role_code FROM user_roles ur "
        " JOIN role_defs rd ON rd.code = ur.role_code AND rd.is_active "
        " WHERE ur.user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


async def _effective_matrix(db: AsyncSession) -> dict[str, set[str]]:
    """role_code -> {permission_key}. Effective = granted UNION locked
    (a lock is a forced grant the UI may not clear)."""
    rows = (await db.execute(text(
        "SELECT role_code, permission_key FROM role_permissions "
        "UNION "
        "SELECT role_code, permission_key FROM role_permission_locks"))).all()
    out: dict[str, set[str]] = {}
    for role_code, key in rows:
        out.setdefault(role_code, set()).add(key)
    return out


async def role_matrix(db: AsyncSession) -> dict[str, dict[str, bool]]:
    """The full role x key boolean grid: every role_defs code -> every
    permission_defs key -> whether it's granted (granted UNION locked).

    Dense (every cell present, False when ungranted) — callers that render
    the whole matrix (e.g. the config UI shape the booking frontend reads)
    rely on that. `effective_permissions` is the per-user analogue.

    Every role_defs row is included regardless of is_active — the config UI
    this replaces (epms's GET /config/role-permissions) shows inactive roles
    too (greyed out), unlike user_role_codes/effective_permissions which
    correctly exclude inactive roles from a live user's grants.
    """
    roles = (await db.execute(text("SELECT code FROM role_defs"))).scalars().all()
    keys = (await db.execute(text("SELECT key FROM permission_defs"))).scalars().all()
    granted = await _effective_matrix(db)  # reuse the one private helper
    return {r: {k: (k in granted.get(r, set())) for k in keys} for r in roles}


async def effective_permissions(
    db: AsyncSession, user_id: uuid.UUID, base_role: str
) -> dict[str, bool]:
    """Every known permission key -> whether ANY of the user's roles grants it.

    Keys come from permission_defs so an ungranted key reads False rather than
    being absent — callers can `.get(k)` without worrying which it is.
    """
    keys = (await db.execute(text("SELECT key FROM permission_defs"))).scalars().all()
    codes = await user_role_codes(db, user_id, base_role)
    matrix = await _effective_matrix(db)
    granted: set[str] = set()
    for code in codes:
        granted |= matrix.get(code, set())
    return {k: (k in granted) for k in keys}


async def has_permission(db: AsyncSession, user_id: uuid.UUID, base_role: str, key: str) -> bool:
    """Boolean gate for non-endpoint (crud-layer) callers.

    Identical admission semantics to require_permission(): system_admin
    short-circuits, otherwise the user's role union (primary ∪ user_roles)
    must have `key` in the effective matrix (granted ∪ locked). Use this in
    crud functions where a FastAPI Depends() doesn't fit — do NOT hand-roll
    the short-circuit against effective_permissions(), which omits it.
    """
    if base_role == "system_admin":
        return True
    codes = await user_role_codes(db, user_id, base_role)
    matrix = await _effective_matrix(db)
    return any(key in matrix.get(c, set()) for c in codes)


# NOTE: the gate itself (require_permission) cannot live at module level —
# each service wires in its OWN get_db / token-payload dependencies, whose
# names differ across services. It is produced by bind() — see below.


def bind(get_db_dep: Callable, get_user_dep: Callable) -> Callable:
    """Bind this service's own DB-session and token-payload dependencies once,
    and get back a require_permission(key) factory for that service.

    Usage (in each service's app/core/authz.py):
        from uniops_authz import bind
        from app.core.deps import get_session, get_token_payload
        require_permission = bind(get_session, get_token_payload)
    """
    def require_permission(key: str) -> Callable:
        async def _check(
            payload: Annotated[dict, Depends(get_user_dep)],
            db: Annotated[AsyncSession, Depends(get_db_dep)],
        ) -> dict:
            role = payload.get("role", "")
            if role == "system_admin":
                return payload
            uid = uuid.UUID(payload["sub"])
            codes = await user_role_codes(db, uid, role)
            matrix = await _effective_matrix(db)
            for code in codes:
                if key in matrix.get(code, set()):
                    return payload
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return _check
    return require_permission
