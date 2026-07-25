"""Generic, idempotent loader driven by the registry.

Header rows upsert by qbo_id (on_conflict_do_update). Line rows are
delete-then-insert per parent to avoid line drift across reloads.
"""
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qbo import QboRaw
from scripts.qbo_import.mappers import last_updated
from scripts.qbo_import.registry import by_name


async def _upsert(db: AsyncSession, model, rows: list[dict]) -> dict:
    if not rows:
        return {"inserted": 0, "updated": 0}
    pk = "qbo_id"
    existing = set(
        (await db.execute(select(getattr(model, pk)))).scalars().all()
    )
    ins = updated = 0
    for r in rows:
        stmt = insert(model).values(**r)
        update_cols = {k: stmt.excluded[k] for k in r if k != pk}
        stmt = stmt.on_conflict_do_update(index_elements=[pk], set_=update_cols)
        await db.execute(stmt)
        if r[pk] in existing:
            updated += 1
        else:
            ins += 1
    return {"inserted": ins, "updated": updated}


async def _load_raw(db: AsyncSession, entity_type: str, objects: list[dict]) -> dict:
    if not objects:
        return {"inserted": 0, "updated": 0}
    existing = set((await db.execute(
        select(QboRaw.qbo_id).where(QboRaw.entity_type == entity_type)
    )).scalars().all())
    ins = updated = 0
    for o in objects:
        row = {"entity_type": entity_type, "qbo_id": o["Id"],
               "last_updated_time": last_updated(o), "payload": o}
        stmt = insert(QboRaw).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["entity_type", "qbo_id"],
            set_={"payload": stmt.excluded.payload,
                  "last_updated_time": stmt.excluded.last_updated_time})
        await db.execute(stmt)
        if o["Id"] in existing:
            updated += 1
        else:
            ins += 1
    await db.commit()
    db.expire_all()
    return {"inserted": ins, "updated": updated}


async def load_entity(db: AsyncSession, name: str, objects: list[dict]) -> dict:
    """Load one entity's raw objects. Returns {inserted, updated}."""
    ent = by_name(name)
    if ent.raw_only:
        return await _load_raw(db, name, objects)
    headers = [ent.header(o) for o in objects]
    counts = await _upsert(db, ent.model, headers)

    if ent.line_model and ent.line:
        parent_ids = [h["qbo_id"] for h in headers]
        await db.execute(
            delete(ent.line_model).where(ent.line_model.parent_qbo_id.in_(parent_ids))
        )
        line_rows = [
            ent.line(ln, obj["Id"])
            for obj in objects
            for ln in (obj.get("Line") or [])
        ]
        for lr in line_rows:
            await db.execute(insert(ent.line_model).values(**lr))
    await db.commit()
    # Header/line rows above are written via Core inserts, which bypass the
    # ORM identity map — with expire_on_commit=False (as tests configure the
    # session), any already-loaded ORM instance for an upserted pk would
    # otherwise keep serving its stale pre-update attributes.
    db.expire_all()
    return counts
