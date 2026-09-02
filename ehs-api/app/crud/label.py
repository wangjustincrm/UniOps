"""Drafting, approving and printing a workplace label.

One rule shapes all of it: nothing prints until a person has approved it. HSE's
answer to question 6.8 was "HSE approval required before any label can be
printed", and the gate is a status on the record rather than a confidence
threshold on an extraction — because a threshold is a machine deciding, which
is the thing being ruled out.

Approving is deliberately a separate act from editing. Somebody who fixes a
field has demonstrated they read that field; somebody who approves is saying
they read all of them. Collapsing the two into "save and print" would make the
approval automatic for anybody who touched the form.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chemical import Chemical
from app.models.label import ChemicalLabel
from app.models.mirrors import User
from app.crud import chemical as chemical_crud


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def list_labels(
    db: AsyncSession, *, chemical_id: uuid.UUID | None = None,
    status_in: list[str] | None = None,
) -> list[ChemicalLabel]:
    stmt = select(ChemicalLabel)
    if chemical_id:
        stmt = stmt.where(ChemicalLabel.chemical_id == chemical_id)
    if status_in:
        stmt = stmt.where(ChemicalLabel.status.in_(status_in))
    return list((await db.execute(
        stmt.order_by(ChemicalLabel.created_at.desc()))).scalars())


async def get_label(db: AsyncSession, label_id: uuid.UUID) -> ChemicalLabel:
    label = await db.get(ChemicalLabel, label_id)
    if label is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Label not found")
    return label


async def draft_from_chemical(
    db: AsyncSession, chemical: Chemical, *, creator_id: uuid.UUID,
) -> ChemicalLabel:
    """A starting point built from what the inventory already knows.

    This is the path that works with no extraction configured at all, and it is
    not a poor relation: the product name, supplier, signal word, pictograms,
    PPE and first-aid note were all entered against the chemical when it was
    registered. What is missing is the H- and P-statements, which only the data
    sheet has.
    """
    sheet = await chemical_crud.current_sheet(db, chemical.id)
    label = ChemicalLabel(
        id=uuid.uuid4(),
        chemical_id=chemical.id,
        sds_id=sheet.id if sheet else None,
        product_identifier=chemical.product_name or chemical.name,
        supplier=chemical.supplier or chemical.manufacturer,
        signal_word=chemical.signal_word,
        pictograms=[h.get("label") for h in (chemical.hazard_classes or [])
                    if isinstance(h, dict) and h.get("label")],
        first_aid=chemical.first_aid_note,
        ppe_note=chemical.ppe_required,
        source="chemical_record",
        created_by=creator_id,
    )
    db.add(label)
    await db.flush()
    return label


async def apply_extraction(
    db: AsyncSession, label: ChemicalLabel, result: dict,
) -> ChemicalLabel:
    """Fill a draft in from what was read off the data sheet.

    Only fills what is empty. Somebody who has already corrected a field has
    made a judgement, and an extraction re-run must not quietly undo it — that
    is the failure mode where a reviewer fixes the pictograms, presses the
    button again out of habit, and prints the original.
    """
    if label.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This label has been approved. Start a new draft rather than "
            "changing what was signed off.")

    values = result.get("values") or {}

    def fill(field: str, value) -> None:
        current = getattr(label, field)
        empty = current in (None, "", [], {})
        if empty and value not in (None, "", [], {}):
            setattr(label, field, value)

    fill("product_identifier", values.get("product_identifier"))
    fill("supplier", values.get("supplier"))
    fill("signal_word", values.get("signal_word"))
    fill("pictograms", values.get("pictograms"))
    fill("hazard_statements", values.get("hazard_statements"))
    fill("precautionary_statements", values.get("precautionary_statements"))
    fill("first_aid", values.get("first_aid"))
    fill("ppe_note", values.get("ppe_note"))

    label.source = "sds_extraction"
    label.extraction = result
    await db.flush()
    return label


async def update_label(db: AsyncSession, label: ChemicalLabel, fields: dict) -> ChemicalLabel:
    if label.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This label has been approved and is not edited. Start a new draft.")
    for key, value in fields.items():
        setattr(label, key, value)
    # Hand corrections are worth recording as such: a label that was extracted
    # and then rewritten is not the same provenance as one taken as read.
    if label.source == "sds_extraction" and fields:
        label.source = "manual"
    await db.flush()
    return label


async def missing_for_approval(label: ChemicalLabel) -> list[str]:
    """What is not yet on the label. Shown; never enforced beyond the two that
    make it not a label at all."""
    gaps: list[str] = []
    if not (label.product_identifier or "").strip():
        gaps.append("the product identifier")
    if not label.pictograms and not label.hazard_statements:
        gaps.append("either a pictogram or a hazard statement")
    return gaps


async def approve(
    db: AsyncSession, label: ChemicalLabel, *, user_id: uuid.UUID,
) -> ChemicalLabel:
    """The gate. A person says they have read it against the data sheet."""
    if label.status == "approved":
        return label
    if label.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"This label is {label.status}")

    gaps = await missing_for_approval(label)
    if gaps:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This is not yet a label — it is missing " + " and ".join(gaps) + ".")

    label.status = "approved"
    label.approved_at = datetime.now(timezone.utc)
    label.approved_by = user_id
    label.approved_by_name = await _name(db, user_id)

    # One approved label per product. An older one still on a shelf should be
    # replaced, and leaving two approved invites printing whichever came up
    # first in a list.
    for other in await list_labels(db, chemical_id=label.chemical_id):
        if other.id != label.id and other.status in ("approved", "printed"):
            other.status = "superseded"

    await db.flush()
    return label


async def record_print(db: AsyncSession, label: ChemicalLabel) -> ChemicalLabel:
    """Count a print. Refuses anything not approved — this is the gate."""
    if label.status not in ("approved", "printed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This label has not been approved. Nothing prints until somebody "
            "has checked it against the data sheet.")
    label.status = "printed"
    label.print_count += 1
    label.last_printed_at = datetime.now(timezone.utc)
    await db.flush()
    return label
