# scripts/qbo_import/orchestrator.py
"""Drive a full/incremental sync across entities, recording progress + watermarks.

Soft-delete (full mode only): any local, not-already-deleted qbo_id absent from
QBO's returned set is stamped deleted_at — the record is preserved, never removed.
"""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qbo import QboSyncRun
from scripts.qbo_import.extract import extract_entity
from scripts.qbo_import.load import load_entity
from scripts.qbo_import.mappers import to_dt
from scripts.qbo_import.registry import by_name

# AP-core default order (masters before transactions).
DEFAULT_ENTITIES = ["Account", "Vendor", "Bill", "BillPayment", "VendorCredit"]


def _max_watermark(objects: list[dict]) -> str | None:
    """Return the original LastUpdatedTime string with the latest instant.

    Compares by PARSED datetime (not lexically) — QBO's UTC offset shifts with
    DST (e.g. Toronto -04:00 summer / -05:00 winter), so a chronologically
    later timestamp can sort lexically smaller than an earlier one.
    """
    best_dt = None
    best_str = None
    for o in objects:
        lu = (o.get("MetaData") or {}).get("LastUpdatedTime")
        if not lu:
            continue
        dt = to_dt(lu)
        if dt is not None and (best_dt is None or dt > best_dt):
            best_dt = dt
            best_str = lu
    return best_str


async def _soft_delete_extras(db: AsyncSession, name: str, seen_ids: set[str]) -> int:
    ent = by_name(name)
    model = ent.model
    local = set((await db.execute(select(model.qbo_id).where(model.deleted_at.is_(None)))).scalars().all())
    gone = local - seen_ids
    if gone:
        await db.execute(
            update(model).where(model.qbo_id.in_(gone)).values(deleted_at=datetime.now(timezone.utc))
        )
        await db.commit()
    db.expire_all()
    return len(gone)


async def run_sync(db: AsyncSession, client, mode: str, entities: list[str] | None = None,
                   started_by=None) -> QboSyncRun:
    entities = entities or DEFAULT_ENTITIES
    run = QboSyncRun(mode=mode, status="running", started_by=started_by,
                     started_at=datetime.now(timezone.utc), counters={}, watermarks={})
    db.add(run)
    await db.commit()
    try:
        prior = {}
        if mode == "incremental":
            last = (await db.execute(
                select(QboSyncRun).where(QboSyncRun.status == "success")
                .order_by(QboSyncRun.started_at.desc()).limit(1)
            )).scalars().first()
            prior = (last.watermarks if last else {}) or {}

        counters, watermarks = {}, {}
        for name in entities:
            since = prior.get(name) if mode == "incremental" else None
            objects = extract_entity(client, name, since=since)
            counts = await load_entity(db, name, objects)
            if mode == "full":
                counts["deleted"] = await _soft_delete_extras(
                    db, name, {o["Id"] for o in objects}
                )
            counters[name] = counts
            wm = _max_watermark(objects)
            watermarks[name] = wm or prior.get(name)

        run.status = "success"
        run.counters = counters
        run.watermarks = watermarks
    except Exception as exc:  # noqa: BLE001 — record failure on the run row
        run.status = "failed"
        run.error = str(exc)[:2000]
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return run
