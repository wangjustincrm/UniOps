"""Sales' principal status (platform/principal.py): the shared users table, read only (as integrations/epms_users.py
reads it for names) — active, inactive, or missing. Set at startup behind the cache; nothing is written."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.integrations.epms_users import USERS
from app.platform.principal import ACTIVE, INACTIVE, MISSING


class UsersTablePrincipalStatus:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def status(self, user_id: uuid.UUID) -> str:
        async with self.session_factory() as s:
            active = (await s.execute(select(USERS.c.is_active).where(USERS.c.id == user_id))).scalar_one_or_none()
        if active is None:
            return MISSING
        return ACTIVE if active else INACTIVE
