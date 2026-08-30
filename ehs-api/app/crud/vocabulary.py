"""Vocabulary reads and edits.

The two rules the whole design rests on are enforced here rather than left to
callers:

Entries are retired, never deleted. Once a cause has been recorded on an
investigation it has to keep existing, or the investigation stops making sense.
There is no hard-delete path — DELETE sets is_active to false.

Locked vocabularies cannot gain or lose entries. The hierarchy of controls is
an international standard, and adding a fourth injury class would make this
year's injury rates incomparable with last year's. Their labels can still be
edited, because wording is a local matter.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vocabulary import Vocabulary, VocabularyItem
from app.schemas.settings import VocabularyItemIn, VocabularyItemPatch


async def list_vocabularies(db: AsyncSession) -> list[tuple[Vocabulary, int, int]]:
    total = func.count(VocabularyItem.id)
    active = func.count(VocabularyItem.id).filter(VocabularyItem.is_active.is_(True))
    rows = (await db.execute(
        select(Vocabulary, total, active)
        .outerjoin(VocabularyItem, VocabularyItem.vocabulary_code == Vocabulary.code)
        .group_by(Vocabulary.code)
        .order_by(Vocabulary.name)
    )).all()
    return [(v, t or 0, a or 0) for v, t, a in rows]


async def get_vocabulary(db: AsyncSession, code: str) -> Vocabulary:
    vocab = (await db.execute(
        select(Vocabulary).where(Vocabulary.code == code)
    )).scalar_one_or_none()
    if vocab is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No vocabulary named {code!r}")
    return vocab


async def list_items(
    db: AsyncSession, code: str, *, include_inactive: bool = False
) -> list[VocabularyItem]:
    stmt = select(VocabularyItem).where(VocabularyItem.vocabulary_code == code)
    if not include_inactive:
        stmt = stmt.where(VocabularyItem.is_active.is_(True))
    return list((await db.execute(
        stmt.order_by(VocabularyItem.sort_order, VocabularyItem.label)
    )).scalars())


async def _path_for(db: AsyncSession, parent_id: uuid.UUID | None, label: str) -> str:
    if parent_id is None:
        return label
    parent = (await db.execute(
        select(VocabularyItem).where(VocabularyItem.id == parent_id)
    )).scalar_one_or_none()
    if parent is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Parent entry not found")
    return f"{parent.path or parent.label}/{label}"


async def add_item(db: AsyncSession, code: str, payload: VocabularyItemIn) -> VocabularyItem:
    vocab = await get_vocabulary(db, code)
    if vocab.is_system_locked:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{vocab.name} is a fixed list — its entries drive statutory "
            "reporting or follow an external standard, so they cannot be added to",
        )
    if payload.parent_id is not None and not vocab.is_hierarchical:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{vocab.name} is a flat list; it has no parent entries",
        )
    clash = (await db.execute(
        select(VocabularyItem).where(
            VocabularyItem.vocabulary_code == code, VocabularyItem.code == payload.code
        )
    )).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{vocab.name} already has an entry coded {payload.code!r}"
            + ("; it is retired — reactivate it instead of adding a duplicate"
               if not clash.is_active else ""),
        )
    item = VocabularyItem(
        id=uuid.uuid4(),
        vocabulary_code=code,
        parent_id=payload.parent_id,
        code=payload.code,
        label=payload.label,
        sort_order=payload.sort_order,
        attrs=payload.attrs,
        path=await _path_for(db, payload.parent_id, payload.label),
    )
    db.add(item)
    await db.flush()
    return item


async def get_item(db: AsyncSession, code: str, item_id: uuid.UUID) -> VocabularyItem:
    item = (await db.execute(
        select(VocabularyItem).where(
            VocabularyItem.id == item_id, VocabularyItem.vocabulary_code == code
        )
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry not found")
    return item


async def patch_item(
    db: AsyncSession, code: str, item_id: uuid.UUID, payload: VocabularyItemPatch
) -> VocabularyItem:
    vocab = await get_vocabulary(db, code)
    item = await get_item(db, code, item_id)

    if vocab.is_system_locked and payload.is_active is False:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{vocab.name} is a fixed list — retiring an entry would change what "
            "the injury rates mean. Its wording can still be edited.",
        )

    if payload.label is not None:
        item.label = payload.label
        item.path = await _path_for(db, item.parent_id, payload.label)
    if payload.parent_id is not None:
        if payload.parent_id == item.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "An entry cannot be its own parent")
        item.parent_id = payload.parent_id
        item.path = await _path_for(db, payload.parent_id, item.label)
    if payload.sort_order is not None:
        item.sort_order = payload.sort_order
    if payload.is_active is not None:
        item.is_active = payload.is_active
    if payload.attrs is not None:
        item.attrs = payload.attrs

    await db.flush()
    return item


async def retire_item(db: AsyncSession, code: str, item_id: uuid.UUID) -> VocabularyItem:
    """The only form of deletion there is. Historical records keep working."""
    return await patch_item(db, code, item_id, VocabularyItemPatch(is_active=False))
