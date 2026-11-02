"""The shared `users` table (owned by epms-api), read only: who a notification
rule can name and the address its email goes to. Declared on its own MetaData
like the shared tasks table, so it never reaches this service's migrations."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Boolean, Column, MetaData, String, Table, or_, select
from sqlalchemy.dialects.postgresql import UUID


def shared_users_table(metadata: MetaData | None = None) -> Table:
    return Table(
        "users", metadata or MetaData(),
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("email", String(255), nullable=False),
        Column("full_name", String(255), nullable=False),
        Column("is_active", Boolean, nullable=False),
    )


USERS = shared_users_table()


@dataclass(frozen=True)
class UserRef:
    id: uuid.UUID
    name: str
    email: str
    active: bool


async def find_users(session, ids: list[uuid.UUID]) -> dict[uuid.UUID, UserRef]:
    if not ids:
        return {}
    rows = (await session.execute(select(USERS).where(USERS.c.id.in_(ids)))).mappings()
    return {r["id"]: UserRef(r["id"], r["full_name"], r["email"], r["is_active"]) for r in rows}


async def search_users(session, q: str = "", limit: int = 20) -> list[UserRef]:
    """Active users by name or email (the rules page's picker)."""
    stmt = select(USERS).where(USERS.c.is_active.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(USERS.c.full_name.ilike(like), USERS.c.email.ilike(like)))
    rows = (await session.execute(stmt.order_by(USERS.c.full_name).limit(limit))).mappings()
    return [UserRef(r["id"], r["full_name"], r["email"], r["is_active"]) for r in rows]
