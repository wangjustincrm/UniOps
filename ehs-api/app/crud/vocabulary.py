"""Vocabulary reads and edits.

The two rules the whole design rests on are enforced here rather than left to
callers:

Entries are retired, never deleted. Once a cause has been recorded on an
investigation it has to keep existing, or the investigation stops making sense.
There is no hard-delete path — DELETE sets is_active to false.

Lists that are trees (round 10: "a master hazard … and a sub hazard(s) under
it", Kirk's example three deep) keep these, so a tree cannot be left in a
state no picker can show:
- a child goes under an entry of the same list that is in use, at most
  MAX_DEPTH deep;
- retiring an entry retires everything under it, and an entry cannot come
  back while the one above it is retired — a live child of a retired group
  is reachable by search and by nothing else;
- renaming or moving an entry rewrites the path of everything under it.

- an entry's trail, and every trail under it, fits TRAIL_MAX — the width
  of the columns a record snapshots it into (and so path's 500 too);
- restoring an entry restores that entry only. What sits under it may have
  been retired on its own before, and bringing it back with its group would
  revive it (review, uniops-8b); each is restored by hand.

What a record keeps is the entry's trail ("Quality › Sanitization process ›
Not followed"), because "Not followed" on its own says nothing; see `trail`.

Locked vocabularies cannot gain or lose entries. The hierarchy of controls is
an international standard, and adding a fourth injury class would make this
year's injury rates incomparable with last year's. Their labels can still be
edited, because wording is a local matter.
"""
from __future__ import annotations

import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vocabulary import Vocabulary, VocabularyItem
from app.schemas.settings import VocabularyItemIn, VocabularyItemPatch


MAX_DEPTH = 4
TRAIL_SEP = " › "
# ehs_causes.label, ehs_hazards.hazard_type_label, ehs_drills.root_cause_label,
# ehs_risk_assessments.hazard_label are all String(200).
TRAIL_MAX = 200


def trails(items: list[VocabularyItem]) -> dict[uuid.UUID, str]:
    """Each entry's labels from the top down, from the parent chain.

    Not from `path`: that is joined with '/', and labels contain '/' —
    "Physical/slips/trips/falls" is two levels, not four."""
    by_id = {i.id: i for i in items}
    out: dict[uuid.UUID, str] = {}

    def walk(i: VocabularyItem, seen: set) -> str:
        if i.id in out:
            return out[i.id]
        parent = by_id.get(i.parent_id) if i.parent_id else None
        if parent is None or parent.id in seen:
            t = i.label
        else:
            t = walk(parent, seen | {i.id}) + TRAIL_SEP + i.label
        out[i.id] = t
        return t

    for i in items:
        walk(i, set())
    return out


async def trail(db: AsyncSession, item: VocabularyItem) -> str:
    """What a record snapshots for this entry."""
    parts, seen, cur = [item.label], {item.id}, item
    while cur.parent_id and cur.parent_id not in seen:
        cur = await db.get(VocabularyItem, cur.parent_id)
        if cur is None:
            break
        seen.add(cur.id)
        parts.append(cur.label)
    return TRAIL_SEP.join(reversed(parts))


async def label_for(db: AsyncSession, vocabulary: str, item_id: uuid.UUID) -> str:
    """★ The one place a record's snapshot of an entry comes from. Whatever
    label a client sent is not used: it is whatever that client displayed."""
    item = await db.get(VocabularyItem, item_id)
    if item is None or item.vocabulary_code != vocabulary:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Not on the {vocabulary.replace('_', ' ')} list: {item_id}")
    return await trail(db, item)


async def _check_fits(db: AsyncSession, prefix: str | None, label: str,
                      item_id: uuid.UUID | None) -> None:
    """The trail this entry would have, and each one under it, within TRAIL_MAX."""
    def too_long(what: str) -> HTTPException:
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"“{what}” would read {TRAIL_MAX}+ characters with the groups above it; "
            "shorten it or a group above it")
    mine = f"{prefix}{TRAIL_SEP}{label}" if prefix else label
    if len(mine) > TRAIL_MAX:
        raise too_long(label)

    async def below(parent_id: uuid.UUID, parent_trail: str) -> None:
        for k in await _children(db, parent_id):
            t = parent_trail + TRAIL_SEP + k.label
            if len(t) > TRAIL_MAX:
                raise too_long(k.label)
            await below(k.id, t)
    if item_id is not None:
        await below(item_id, mine)


async def _children(db: AsyncSession, item_id: uuid.UUID) -> list[VocabularyItem]:
    return list((await db.execute(
        select(VocabularyItem).where(VocabularyItem.parent_id == item_id))).scalars())


async def _depth(db: AsyncSession, item: VocabularyItem) -> int:
    """1 for a top-level entry."""
    n, seen, cur = 1, {item.id}, item
    while cur.parent_id and cur.parent_id not in seen:
        cur = await db.get(VocabularyItem, cur.parent_id)
        if cur is None:
            break
        seen.add(cur.id)
        n += 1
    return n


async def _height(db: AsyncSession, item: VocabularyItem) -> int:
    """Levels from this entry down to its deepest descendant, itself included."""
    kids = await _children(db, item.id)
    return 1 + max([await _height(db, k) for k in kids], default=0)


async def _check_parent(db: AsyncSession, vocab: Vocabulary, parent_id: uuid.UUID,
                        *, levels_below: int = 1, moving: uuid.UUID | None = None) -> VocabularyItem:
    if not vocab.is_hierarchical:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{vocab.name} is a flat list; it has no parent entries")
    parent = await db.get(VocabularyItem, parent_id)
    if parent is None or parent.vocabulary_code != vocab.code:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"That parent entry is not on the {vocab.name} list")
    if not parent.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"“{parent.label}” is retired; restore it before adding under it")
    if moving is not None:
        cur, seen = parent, set()
        while cur is not None and cur.id not in seen:
            if cur.id == moving:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    "An entry cannot go under itself or under something inside it")
            seen.add(cur.id)
            cur = await db.get(VocabularyItem, cur.parent_id) if cur.parent_id else None
    if await _depth(db, parent) + levels_below > MAX_DEPTH:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"{vocab.name} goes at most {MAX_DEPTH} levels deep")
    return parent


async def _derive_code(db: AsyncSession, code: str, label: str) -> str:
    """HSE name entries; a code typed by hand is one more thing to invent."""
    base = re.sub(r"[^A-Z0-9]+", "_", label.upper()).strip("_")[:50] or "ENTRY"
    taken = set((await db.execute(select(VocabularyItem.code).where(
        VocabularyItem.vocabulary_code == code,
        VocabularyItem.code.like(f"{base}%")))).scalars())
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


async def _repath_below(db: AsyncSession, item: VocabularyItem) -> None:
    for k in await _children(db, item.id):
        k.path = f"{item.path or item.label}/{k.label}"
        await _repath_below(db, k)


async def _set_active_below(db: AsyncSession, item: VocabularyItem, active: bool) -> None:
    for k in await _children(db, item.id):
        k.is_active = active
        await _set_active_below(db, k, active)


# ★ Every column that holds a list entry's id. A new one belongs here too, or
# HSE are told nothing uses an entry that something does (review, uniops-8b:
# "count by id across every table that references it — do not miss one").
_REFERENCES: tuple[tuple[str, str], ...] = (
    ("Causes on investigations and hazards",
     "select count(*) from ehs_causes where vocabulary_item_id = :id"),
    ("Investigations (hazards identified)",
     "select count(*) from ehs_incident_investigations where jsonb_exists(identified_hazard_ids, cast(:sid as text))"),
    ("Investigations (PPE that could have prevented it)",
     "select count(*) from ehs_incident_investigations where jsonb_exists(ppe_that_could_prevent, cast(:sid as text))"),
    ("Hazard register (type)",
     "select count(*) from ehs_hazards where hazard_type_id = :id"),
    ("Risk assessments (hazard)",
     "select count(*) from ehs_risk_assessments where hazard_item_id = :id"),
    ("Risk assessments (controls)",
     "select count(*) from ehs_risk_assessments where hazard_controls @> jsonb_build_array(jsonb_build_object('id', cast(:sid as text)))"
     " or hierarchy_controls @> jsonb_build_array(jsonb_build_object('id', cast(:sid as text)))"),
    ("Drills (root cause)",
     "select count(*) from ehs_drills where root_cause_id = :id"),
    ("Form answers",
     "select count(*) from ehs_form_answers where jsonb_exists(value_ids, cast(:sid as text))"),
    ("Position requirements",
     "select count(*) from ehs_position_requirements where vocabulary_item_id = :id"),
    ("Incidents (category)",
     "select count(*) from ehs_incidents where category_id = :id"),
    ("Corrective actions (category)",
     "select count(*) from ehs_actions where category_id = :id"),
    ("People on incidents (body part, injury)",
     "select count(*) from ehs_incident_persons where body_part_id = :id or nature_of_injury_id = :id"),
    ("First aid log (body part)",
     "select count(*) from ehs_first_aid_log where body_part_id = :id"),
    ("JHSC recommendations (status)",
     "select count(*) from ehs_jhsc_recommendations where status_item_id = :id"),
    ("Safety equipment (type)",
     "select count(*) from ehs_safety_assets where asset_type_id = :id"),
)


async def usage(db: AsyncSession, item_id: uuid.UUID) -> list[dict]:
    """Which records name this entry, by kind — for "it will become a group"."""
    from sqlalchemy import text
    out = []
    for what, sql in _REFERENCES:
        n = (await db.execute(text(sql), {"id": item_id, "sid": str(item_id)})).scalar() or 0
        if n:
            out.append({"what": what, "count": n})
    return out


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
    prefix = None
    if payload.parent_id is not None:
        parent = await _check_parent(db, vocab, payload.parent_id)
        prefix = await trail(db, parent)
    await _check_fits(db, prefix, payload.label, None)
    item_code = payload.code or await _derive_code(db, code, payload.label)
    clash = (await db.execute(
        select(VocabularyItem).where(
            VocabularyItem.vocabulary_code == code, VocabularyItem.code == item_code
        )
    )).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{vocab.name} already has an entry coded {item_code!r}"
            + ("; it is retired — reactivate it instead of adding a duplicate"
               if not clash.is_active else ""),
        )
    item = VocabularyItem(
        id=uuid.uuid4(),
        vocabulary_code=code,
        parent_id=payload.parent_id,
        code=item_code,
        label=payload.label,
        sort_order=payload.sort_order,
        attrs=payload.attrs,
        path=await _path_for(db, payload.parent_id, payload.label),
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        # Two people adding the same wording at once: the derived code collides.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{vocab.name} just gained an entry coded {item_code!r}; "
                            "reload and check before adding it again") from None
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

    moving = payload.parent_id is not None and payload.parent_id != item.parent_id
    if payload.label is not None or moving:
        new_parent = (await _check_parent(db, vocab, payload.parent_id,
                                          levels_below=await _height(db, item), moving=item.id)
                      if moving else
                      (await db.get(VocabularyItem, item.parent_id) if item.parent_id else None))
        await _check_fits(db, await trail(db, new_parent) if new_parent else None,
                          payload.label or item.label, item.id)
    if payload.label is not None:
        item.label = payload.label
        item.path = await _path_for(db, item.parent_id, payload.label)
        await _repath_below(db, item)
    if moving:
        item.parent_id = payload.parent_id
        item.path = await _path_for(db, payload.parent_id, item.label)
        await _repath_below(db, item)
    if payload.sort_order is not None:
        item.sort_order = payload.sort_order
    if payload.is_active is True and not item.is_active and item.parent_id:
        parent = await db.get(VocabularyItem, item.parent_id)
        if parent is not None and not parent.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"“{parent.label}” above it is retired; restore that first")
    if payload.is_active is not None:
        item.is_active = payload.is_active
        if payload.is_active is False:
            await _set_active_below(db, item, False)
    if payload.attrs is not None:
        item.attrs = payload.attrs

    await db.flush()
    return item


async def retire_item(db: AsyncSession, code: str, item_id: uuid.UUID) -> VocabularyItem:
    """The only form of deletion there is. Historical records keep working."""
    return await patch_item(db, code, item_id, VocabularyItemPatch(is_active=False))
