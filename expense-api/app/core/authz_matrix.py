"""Access Control matrix reads for expense-api.

⚠️ SIBLING COPY of packages/authz/uniops_authz/core.py — keep the two in step.
expense-api is not wired to that package: its Docker build context is
./expense-api (docker-compose.prod.yml), so `COPY packages/authz` — the line
every other service's Dockerfile uses — has nothing to copy from. The matrix
lives in the same physical database as this service's own tables, so the two
reads below are reproduced here verbatim rather than reached over HTTP. Same
precedent as budget_scope.py, which ships byte-identical in budget-api and
finance-api for the same reason.

Semantics copied from uniops_authz.core, deliberately, cell for cell:
  * a user's roles are their JWT primary role UNION identity's `user_roles`,
    with inactive `role_defs` excluded;
  * the effective matrix is `role_permissions` UNION `role_permission_locks`
    (a lock is a forced grant the admin UI may not clear);
  * `system_admin` short-circuits every gate.
"""
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """PRIMARY role plus every ADDITIONAL role from identity's user_roles.

    Both sources count: the primary role is a real role, and consulting only
    user_roles would silently lose primary-role holders.
    """
    codes: set[str] = {base_role} if base_role else set()
    rows = (await db.execute(text(
        "SELECT ur.role_code FROM user_roles ur "
        " JOIN role_defs rd ON rd.code = ur.role_code AND rd.is_active "
        " WHERE ur.user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


async def has_permission(db: AsyncSession, user_id: uuid.UUID, base_role: str, key: str) -> bool:
    """Whether ANY role the user holds grants `key` in the effective matrix."""
    if base_role == "system_admin":
        return True
    codes = await user_role_codes(db, user_id, base_role)
    if not codes:
        return False
    rows = (await db.execute(text(
        "SELECT 1 FROM role_permissions WHERE permission_key = :k AND role_code = ANY(:c) "
        "UNION ALL "
        "SELECT 1 FROM role_permission_locks WHERE permission_key = :k AND role_code = ANY(:c) "
        "LIMIT 1"), {"k": key, "c": list(codes)})).first()
    return rows is not None
