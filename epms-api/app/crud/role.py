"""Which role codes a user may be given.

`role_defs` in identity is where roles are defined; every module that adds one
adds it there. This service used to keep its own hardcoded list beside it, and
the two drifted: eleven roles existed that user administration would not accept
— dept_admin, supervisor, director, the five Safety roles, and the two
additional-only finance ones.

The effect was not only that those users could not be created. The admin edit
form submits the role along with everything else, so opening an existing holder
of one and pressing Save — changing nothing — came back 422. Their record was
simply not editable.

Reading the table instead means a role added by a migration works here the day
that migration runs, with nothing to remember.
"""
import logging

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user import FALLBACK_ROLES

logger = logging.getLogger(__name__)


async def valid_roles(db: AsyncSession) -> set[str]:
    """Active role codes, from identity's `role_defs`.

    Retired roles are excluded — `worker` was retired precisely so that nobody
    would be put on it again.

    Additional-only roles are excluded too. `users.role` is somebody's primary
    login role, and identity marks the roles that may not be one —
    erp_pa_officer and payment_officer among them, and the three Safety roles
    that are held alongside a job rather than instead of it. Both conditions
    are read from the same row, so neither can drift from the other.
    """
    try:
        rows = (await db.execute(
            text("SELECT code FROM role_defs WHERE is_active AND assignable_as_primary")
        )).scalars().all()
    except DBAPIError:
        # The table, or `assignable_as_primary` on it, predates this code.
        # Refusing every role would lock every administrator out of user
        # management, which is a worse failure than the stale list this
        # replaces — so fall back to it and say so.
        logger.warning("role_defs unreadable; falling back to the built-in role list")
        return set(FALLBACK_ROLES)
    # Empty means unseeded, not "no role is allowed".
    return set(rows) or set(FALLBACK_ROLES)
