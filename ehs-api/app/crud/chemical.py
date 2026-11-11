"""The chemical inventory, its data sheets, and the two gaps that matter.

Most of this is ordinary. The part worth reading is `compliance_gaps`, because
it is the reason the module exists rather than a spreadsheet: WHMIS makes two
things findings, and neither is visible in a list sorted by name.

  * a hazardous product on site with no data sheet at all, and
  * a sheet more than three years past its revision date.

Both are what an inspector asks for, and both are answered here rather than by
somebody reading down a list.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.label import ChemicalLabel
from app.models.chemical import Chemical, SafetyDataSheet
from app.models.mirrors import Department, Location, User
from app.models.statutory import StatutoryDeadline
from app.services import statutory
from app.services import deadlines as deadline_service
from app.services.numbering import next_number
from app.services.day_range import plant_today


def _new_qr_token() -> str:
    """Unguessable — it is printed on a drum anybody can photograph."""
    return secrets.token_urlsafe(24)


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def _place(db: AsyncSession, location_id: uuid.UUID | None) -> tuple:
    if location_id is None:
        return None, None, None
    loc = await db.get(Location, location_id)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such plant area")
    dept = (await db.execute(
        select(Department.name).where(Department.id == loc.department_id))).scalar()
    return loc.path, loc.department_id, dept


# ── Inventory ────────────────────────────────────────────────────────────────

async def list_chemicals(
    db: AsyncSession, *, search: str | None = None, location_id: uuid.UUID | None = None,
    include_retired: bool = False, limit: int = 400,
) -> list[Chemical]:
    """The inventory, searchable by name or narrowed to an area.

    Both are what HSE asked for — "mobile lookup by name or by area" — because
    the two questions asked of this in anger are "what is this drum" and "what
    is stored in here", and they are asked standing in front of it.
    """
    stmt = select(Chemical)
    if not include_retired:
        stmt = stmt.where(Chemical.is_active.is_(True))
    if location_id:
        stmt = stmt.where(Chemical.location_id == location_id)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(
            Chemical.name.ilike(like),
            Chemical.product_name.ilike(like),
            Chemical.manufacturer.ilike(like),
            Chemical.product_code.ilike(like),
        ))
    return list((await db.execute(stmt.order_by(Chemical.name).limit(limit))).scalars())


async def get_chemical(db: AsyncSession, chemical_id: uuid.UUID) -> Chemical:
    chemical = await db.get(Chemical, chemical_id)
    if chemical is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chemical not found")
    return chemical


async def create_chemical(db: AsyncSession, payload: dict) -> Chemical:
    path, dept_id, dept_name = await _place(db, payload.get("location_id"))
    chemical = Chemical(
        id=uuid.uuid4(),
        chemical_no=await next_number(db, "chemical"),
        name=payload["name"],
        product_name=payload.get("product_name"),
        manufacturer=payload.get("manufacturer"),
        supplier=payload.get("supplier"),
        manufacturer_contact=payload.get("manufacturer_contact"),
        emergency_contact=payload.get("emergency_contact"),
        hazard_statements=payload.get("hazard_statements") or [],
        product_code=payload.get("product_code"),
        hazard_classes=payload.get("hazard_classes") or [],
        signal_word=payload.get("signal_word"),
        physical_state=payload.get("physical_state"),
        location_id=payload.get("location_id"),
        location_path=path, department_id=dept_id, department_name=dept_name,
        storage_note=payload.get("storage_note"),
        container_size=payload.get("container_size"),
        quantity_on_site=payload.get("quantity_on_site"),
        unit=payload.get("unit"),
        ppe_required=payload.get("ppe_required"),
        first_aid_note=payload.get("first_aid_note"),
        notes=payload.get("notes"),
    )
    db.add(chemical)
    await db.flush()
    return chemical


async def update_chemical(db: AsyncSession, chemical: Chemical, fields: dict) -> Chemical:
    was_called = chemical.name
    if "location_id" in fields:
        (chemical.location_path, chemical.department_id,
         chemical.department_name) = await _place(db, fields["location_id"])
    for key, value in fields.items():
        setattr(chemical, key, value)
    await db.flush()
    if fields.get("name") and fields["name"] != was_called:
        await _rename_labels(db, chemical, was_called)
    return chemical


async def _rename_labels(db: AsyncSession, chemical: Chemical, was_called: str) -> None:
    """Correcting the name has to reach the label, including an approved one.

    The label keeps its own copy of the product identifier, which is right —
    it is the record of what was printed. But WHMIS requires that identifier to
    match the product and its data sheet, so once the name is corrected an
    approved label is printing something that no longer matches, and it would
    go on doing so with nothing on screen to say it had gone stale.

    The approval is therefore withdrawn rather than the label left alone or
    silently reprinted under the new name: somebody approved the old wording,
    not this one, and approval before printing is the whole of the control on
    these. It goes back to draft carrying the corrected name, for whoever
    approves it to check against the sheet.
    """
    labels = (await db.execute(
        select(ChemicalLabel).where(ChemicalLabel.chemical_id == chemical.id)
    )).scalars().all()
    # ★ Review of round 12 (C9): one draft at a time. Where a new version is
    # already being drafted, the label in force does not go back to draft
    # beside it — it stops printing (superseded) and the draft carries the
    # corrected name. And a label already printed is withdrawn too: it used to
    # stay printable under the old name, the very thing this exists to stop.
    drafting = any(label.status == "draft" for label in labels)
    for label in labels:
        if label.product_identifier != was_called:
            continue  # already says something else — a hand correction; leave it
        if label.status in ("approved", "printed") and drafting:
            # Kept as printed: it is the record of what went on the drums.
            label.status = "superseded"
            continue
        label.product_identifier = chemical.name
        if label.status in ("approved", "printed"):
            label.status = "draft"
            label.approved_at = None
            label.approved_by = None
            label.approved_by_name = None
            drafting = True
    await db.flush()


async def retire(
    db: AsyncSession, chemical: Chemical, *, user_id: uuid.UUID | None = None,
) -> Chemical:
    """Off site, but not gone.

    An incident two years ago may name this product, and deleting it would
    leave that record pointing at nothing.

    ★ Retiring also stops the three-year review clock on its data sheets. The
    obligation is to hold a current sheet for a product kept on site; once it
    is not kept on site there is nothing to review. This was not done, and the
    two screens disagreed about the same obligation: "Needs attention" counts
    only products on site, while the compliance calendar showed retired ones as
    overdue reviews — two in the dev database, which is how it was found.
    """
    chemical.is_active = False
    sheets = list((await db.execute(
        select(SafetyDataSheet.id)
        .where(SafetyDataSheet.chemical_id == chemical.id))).scalars())
    if sheets:
        open_clocks = (await db.execute(
            select(StatutoryDeadline).where(
                StatutoryDeadline.source_type == "sds",
                StatutoryDeadline.source_id.in_(sheets),
                StatutoryDeadline.satisfied_at.is_(None),
            ))).scalars()
        for deadline in open_clocks:
            # Through the service, because closing the prompt is half of it.
            await deadline_service.satisfy(
                db, deadline,
                # The name as well as the id: the module keeps a name snapshot
                # beside every user reference, and an audit pack that says
                # "satisfied by —" is the reason for the rule.
                user_id=user_id, user_name=await _name(db, user_id),
                evidence_note="Product no longer kept on site",
                when=datetime.now(timezone.utc),
            )
    await db.flush()
    return chemical


# ── Data sheets ──────────────────────────────────────────────────────────────

async def sheets_for(db: AsyncSession, chemical_id: uuid.UUID) -> list[SafetyDataSheet]:
    return list((await db.execute(
        select(SafetyDataSheet).where(SafetyDataSheet.chemical_id == chemical_id)
        .order_by(SafetyDataSheet.is_current.desc(),
                  SafetyDataSheet.revision_date.desc().nullslast()))).scalars())


async def current_sheet(db: AsyncSession, chemical_id: uuid.UUID) -> SafetyDataSheet | None:
    return (await db.execute(
        select(SafetyDataSheet).where(
            SafetyDataSheet.chemical_id == chemical_id,
            SafetyDataSheet.is_current.is_(True),
        ))).scalar_one_or_none()


async def get_sheet(db: AsyncSession, sds_id: uuid.UUID) -> SafetyDataSheet:
    sheet = await db.get(SafetyDataSheet, sds_id)
    if sheet is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data sheet not found")
    return sheet


async def by_qr_token(db: AsyncSession, token: str) -> SafetyDataSheet | None:
    return (await db.execute(
        select(SafetyDataSheet).where(SafetyDataSheet.qr_token == token))).scalar_one_or_none()


async def _supersede_labels(db: AsyncSession, chemical_id: uuid.UUID) -> int:
    """Mark this chemical's live labels as out of date. Returns how many."""
    from app.models.label import ChemicalLabel

    rows = (await db.execute(
        select(ChemicalLabel).where(
            ChemicalLabel.chemical_id == chemical_id,
            ChemicalLabel.status.in_(("approved", "printed")),
        ))).scalars().all()
    for label in rows:
        label.status = "superseded"
    return len(rows)


async def add_sheet(
    db: AsyncSession, chemical: Chemical, payload: dict, *, user_id: uuid.UUID,
) -> SafetyDataSheet:
    """File a new revision. Whatever it replaces stops being current.

    Superseding rather than overwriting: the sheet in force at the time of an
    exposure is part of that incident's record, and a supplier revision must
    not rewrite it.
    """
    for previous in await sheets_for(db, chemical.id):
        if previous.is_current:
            previous.is_current = False
            await _clear_review_clock(db, previous)

    # ★ Any approved label built from the sheet being replaced is now suspect.
    #
    # A supplier revision can change the hazard classification, and the label on
    # the drum is what somebody reads before opening it. WHMIS 2015 wants that
    # label to match the current sheet, so an approved label made from an older
    # one must stop being printable — but it is not rewritten either: the new
    # classification has to be read off the new sheet and approved by a person,
    # which is the rule this module already enforces for every label.
    #
    # Superseded rather than deleted, for the same reason the sheet is: the
    # label that was in force is part of the record.
    await _supersede_labels(db, chemical.id)

    revision_date = payload.get("revision_date")
    sheet = SafetyDataSheet(
        id=uuid.uuid4(),
        chemical_id=chemical.id,
        revision=payload.get("revision"),
        revision_date=revision_date,
        received_on=payload.get("received_on") or plant_today(),
        language=payload.get("language") or "en",
        file_id=payload.get("file_id"),
        filename=payload.get("filename"),
        qr_token=_new_qr_token(),
        uploaded_by=user_id,
        uploaded_by_name=await _name(db, user_id),
    )
    db.add(sheet)
    await db.flush()
    await ensure_review_clock(db, chemical, sheet)
    return sheet


async def ensure_review_clock(
    db: AsyncSession, chemical: Chemical, sheet: SafetyDataSheet
) -> StatutoryDeadline | None:
    """Reg 860's three years, on the same clock table as everything else.

    Runs from the supplier's revision date where there is one. Where there is
    not, it runs from the day the sheet was received — earlier than the
    regulation strictly requires, which is the safe direction to be wrong in.
    """
    starts_from = sheet.revision_date or sheet.received_on or plant_today()
    starts_at = datetime.combine(starts_from, datetime.min.time(),
                                 tzinfo=statutory.ONTARIO)
    due_at = statutory.compute_due("sds_3y", starts_at)
    sheet.review_due_on = due_at.date()

    existing = (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "sds",
            StatutoryDeadline.source_id == sheet.id,
        ))).scalar_one_or_none()
    if existing is not None:
        existing.due_at = due_at
        await db.flush()
        return existing

    _, authority = statutory.RULES["sds_3y"]
    deadline = StatutoryDeadline(
        id=uuid.uuid4(),
        source_type="sds",
        source_id=sheet.id,
        source_ref=f"{chemical.chemical_no} {chemical.name}"[:60],
        kind="sds_3y",
        regulation_ref=authority,
        clock_type="calendar",
        starts_at=starts_at,
        due_at=due_at,
    )
    db.add(deadline)
    await db.flush()
    return deadline


async def _clear_review_clock(db: AsyncSession, sheet: SafetyDataSheet) -> None:
    """A superseded sheet stops being chased.

    Satisfied rather than deleted — the clock ran and was answered by a new
    revision arriving, which is the honest history.
    """
    deadline = (await db.execute(
        select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "sds",
            StatutoryDeadline.source_id == sheet.id,
            StatutoryDeadline.satisfied_at.is_(None),
        ))).scalar_one_or_none()
    if deadline is not None:
        # Through the service rather than by writing the column: satisfying an
        # obligation also closes its prompt, and a second place that only wrote
        # the column left the old "SDS review — overdue" task open for good.
        await deadline_service.satisfy(
            db, deadline,
            user_id=None, user_name=None,
            evidence_note="Superseded by a newer revision",
            when=datetime.now(timezone.utc),
        )


# ── The two findings ─────────────────────────────────────────────────────────

async def compliance_gaps(db: AsyncSession, *, today: date | None = None) -> dict:
    """What an inspector would write up.

    Kept as one call returning both, because they are one question — "is the
    WHMIS programme in order" — and answering it in two round trips invites a
    screen that shows one and forgets the other.
    """
    today = today or plant_today()

    active = list((await db.execute(
        select(Chemical).where(Chemical.is_active.is_(True))
        .order_by(Chemical.name))).scalars())

    current = {
        s.chemical_id: s
        for s in (await db.execute(
            select(SafetyDataSheet).where(SafetyDataSheet.is_current.is_(True)))).scalars()
    }

    no_sheet, overdue = [], []
    for chemical in active:
        sheet = current.get(chemical.id)
        if sheet is None:
            no_sheet.append({
                "id": str(chemical.id), "chemical_no": chemical.chemical_no,
                "name": chemical.name, "location_path": chemical.location_path,
            })
            continue
        if sheet.review_due_on and sheet.review_due_on < today:
            overdue.append({
                "id": str(chemical.id), "chemical_no": chemical.chemical_no,
                "name": chemical.name, "location_path": chemical.location_path,
                "review_due_on": sheet.review_due_on.isoformat(),
                "days_overdue": (today - sheet.review_due_on).days,
            })

    overdue.sort(key=lambda r: -r["days_overdue"])
    return {
        "products": len(active),
        "with_current_sheet": len(active) - len(no_sheet),
        "no_sheet": no_sheet,
        "review_overdue": overdue,
    }


async def by_hazard_class(db: AsyncSession) -> list[dict]:
    """How much of what is on site. Feeds the emergency plan more than the
    dashboard — a fire crew asks what is stored here, not how many products."""
    rows = list((await db.execute(
        select(Chemical).where(Chemical.is_active.is_(True)))).scalars())
    counts: dict[str, int] = {}
    for chemical in rows:
        for entry in chemical.hazard_classes or []:
            label = entry.get("label") if isinstance(entry, dict) else str(entry)
            if label:
                counts[label] = counts.get(label, 0) + 1
    out = [{"label": k, "count": v} for k, v in counts.items()]
    out.sort(key=lambda r: (-r["count"], r["label"]))
    return out


async def count_active(db: AsyncSession) -> int:
    return (await db.execute(
        select(func.count()).select_from(Chemical)
        .where(Chemical.is_active.is_(True)))).scalar() or 0
