"""A record's audit history (FR-OB-53): what changed, when, by whom and why,
newest first. Read with the permission that reads the record itself."""
from __future__ import annotations

from sqlalchemy import select

from app.models.records import AuditRecord
from app.platform.authorization import authorize
from app.schemas.audit import AuditEntryRead

MAX_ENTRIES = 200


async def history(uow, model: type, id: int, *, permission: str) -> list[AuditEntryRead]:
    await authorize(uow, permission)
    rows = (await uow.session.execute(
        select(AuditRecord)
        .where(AuditRecord.entity == model.__tablename__, AuditRecord.entity_id == str(id))
        .order_by(AuditRecord.at.desc(), AuditRecord.id.desc())
        .limit(MAX_ENTRIES)
    )).scalars().all()
    return [AuditEntryRead.model_validate(r) for r in rows]
