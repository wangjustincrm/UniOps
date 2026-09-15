"""Resolving a label's hazard classes to the artwork that prints for them.

GHS pictograms are regulated artwork — a red diamond with a specific symbol,
published by Health Canada for WHMIS 2015. Nothing here draws one. They are
uploaded once against the hazard class in Settings, and this finds them.

Two shapes have to be understood, because both are in the data. The AI reader
writes the vocabulary code (`CORROSIVE`); a label filled in by hand writes the
label text (`Corrosive`). Keying artwork on only one of them would have worked
on half the existing labels and silently printed nothing on the other half.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vocabulary import VocabularyItem

VOCABULARY = "ghs_hazard_class"


@dataclass(frozen=True)
class Pictogram:
    code: str
    label: str
    file_id: uuid.UUID | None

    @property
    def has_artwork(self) -> bool:
        return self.file_id is not None


async def resolve(db: AsyncSession, entries: list | None) -> list[Pictogram]:
    """One Pictogram per entry on the label, in the order the label carries.

    An entry matching no hazard class is kept rather than dropped, with no
    artwork: it is something somebody typed, and losing it silently would hide
    that the label claims a hazard the list does not know about.
    """
    if not entries:
        return []

    items = (await db.execute(
        select(VocabularyItem).where(VocabularyItem.vocabulary_code == VOCABULARY)
    )).scalars().all()
    by_code = {i.code.upper(): i for i in items}
    by_label = {i.label.strip().lower(): i for i in items}

    out: list[Pictogram] = []
    for entry in entries:
        text = str(entry).strip()
        item = by_code.get(text.upper()) or by_label.get(text.lower())
        if item is None:
            out.append(Pictogram(code=text, label=text, file_id=None))
            continue
        raw = (item.attrs or {}).get("pictogram_file_id")
        out.append(Pictogram(
            code=item.code, label=item.label,
            file_id=uuid.UUID(str(raw)) if raw else None,
        ))
    return out


def missing_artwork(pictograms: list[Pictogram]) -> list[str]:
    """Which classes would print without their symbol."""
    return [p.label for p in pictograms if not p.has_artwork]
