"""A piece of equipment named on a record.

Round 10 (open box): "there is an equipment register in the settings that does
not appear in any of the incident forms. add this equipment as a picker on all
forms so that we can identify what piece of equipment contributed to the
incidents. Include it on the hazard register, job analysis, and work permits."

One check for every record that names one: it has to exist, and a new choice
has to be in service. A record keeping the one it already names is left alone
even if that has since been retired — the record says what was there.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.equipment import Equipment


async def check(db: AsyncSession, new_id: uuid.UUID | None,
                current: uuid.UUID | None = None) -> None:
    if new_id is None or new_id == current:
        return
    row = await db.get(Equipment, new_id)
    if row is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No such equipment")
    if not row.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{row.name} has been retired from the equipment register; "
                            "pick another")
