"""The hazard register and job safety analyses.

Two rules that decide most of what is here.

**A hazard's rating comes from a risk assessment, never from a field somebody
typed.** The matrix is the company's document; a register that lets a number be
entered beside a hazard would drift from it within a month. Assessing a hazard
writes a row through the existing risk service and copies the resulting rating
onto the register line, which is what makes the register sortable by risk
without a join.

**A JSA is versioned, and a signature belongs to a version and a day.** Signing
v2 does not carry to v3, and signing on Monday does not carry to Tuesday: the
same analysis is signed afresh each time the job runs, which is the point of
signing it at all.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import String, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hazard import Hazard, Jsa, JsaSignoff, JsaStep
from app.models.mirrors import Department, Location, User
from app.models.risk import RiskAssessment
from app.services.numbering import next_number
from app.services.day_range import plant_today

# Worst first. The register is read top-down, so the order is the point — and
# it is the matrix's order, not the alphabet's, which would put "Acceptable"
# above "Critical" and turn the register upside down.
#
# Keyed lowercase and looked up lowercase: the seeded matrix stores its ratings
# in lower case, and matching on the capitalised spelling silently scored every
# row as unknown, which sorted the register by creation date while looking as
# though it had been ranked.
RATING_ORDER = {"critical": 0, "serious": 1, "moderate": 2, "acceptable": 3}
UNRANKED = 9


def rating_rank(rating: str | None) -> int:
    return RATING_ORDER.get((rating or "").strip().lower(), UNRANKED)


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


# ── Hazard register ──────────────────────────────────────────────────────────

async def list_hazards(
    db: AsyncSession, *, status_in: list[str] | None = None,
    location_id: uuid.UUID | None = None, rating: str | None = None,
    order: str = "newest", assessed_only: bool = False, limit: int = 300,
) -> list[Hazard]:
    """Newest first, or worst first (round 12, HAZ-07).

    ★ Ordered in SQL, before the limit. The rank used to be applied in Python
    to the first 300 rows in no particular order, so past 300 hazards the
    "worst first" list could leave out the worst. The rank is the matrix's
    order, not the alphabet's — "Acceptable" before "Critical" would put the
    register exactly the wrong way up.
    """
    stmt = select(Hazard)
    if status_in:
        stmt = stmt.where(Hazard.status.in_(status_in))
    if location_id:
        stmt = stmt.where(Hazard.location_id == location_id)
    if rating:
        stmt = stmt.where(Hazard.current_rating == rating)
    if assessed_only:
        stmt = stmt.where(Hazard.risk_assessment_id.is_not(None))
    if order == "risk":
        rank = case(RATING_ORDER, value=func.lower(func.trim(Hazard.current_rating)),
                    else_=UNRANKED)
        stmt = stmt.order_by(rank, Hazard.created_at.desc(), Hazard.hazard_no.desc())
    else:
        stmt = stmt.order_by(Hazard.created_at.desc(), Hazard.hazard_no.desc())
    return list((await db.execute(stmt.limit(limit))).scalars())


async def _picked(db: AsyncSession, vocabulary: str, ids: list[uuid.UUID],
                  kept: list[dict] | None = None) -> list[dict]:
    """[{id, label}] for entries picked from one of HSE's lists: as named now if
    new to the record, as recorded if it already had them."""
    from app.api.v1.risk import _snapshot
    return await _snapshot(db, vocabulary, ids, kept)


async def _apply_lists(db: AsyncSession, fields: dict, hazard: Hazard | None = None) -> None:
    """Round 12, HAZ-11: the kinds and controls picked, snapshotted. The first
    kind is also the hazard's single kind, for everything that reads one."""
    ids = fields.pop("hazard_type_ids", None)
    if ids is not None:
        fields["hazard_types"] = await _picked(db, "hazard", ids,
                                               hazard.hazard_types if hazard else None)
        fields["hazard_type_id"] = ids[0] if ids else None
    controls = fields.pop("hazard_control_ids", None)
    if controls is not None:
        fields["hazard_controls"] = await _picked(db, "hazard_control", controls,
                                                  hazard.hazard_controls if hazard else None)


def _rank_expr():
    """How severe, Acceptable 1 to Critical 4, with an unscored hazard as no
    value, so it sits last whichever way the column is sorted. ★ Re-review
    N4: it was the rank (Critical 1st), so the arrow said ascending over a
    list that read worst first. The page sorts Risk descending first."""
    worst = max(RATING_ORDER.values())
    rank = {k: worst - v + 1 for k, v in RATING_ORDER.items()}
    return case(rank, value=func.lower(func.trim(Hazard.current_rating)), else_=None)


def _hazard_sortable():
    from app.crud._list_order import lower_text
    return {
        "hazard_no": Hazard.hazard_no,
        "title": lower_text(Hazard.title),
        "area": lower_text(Hazard.location_path),
        "owner": lower_text(Hazard.owner_name),
        # Descending is worst first ("highest risk first", HAZ-07).
        "risk": _rank_expr(),
        "status": Hazard.status,
        "recorded": Hazard.created_at,
        "review_due": Hazard.review_due_on,
    }


async def hazard_register(
    db: AsyncSession, *, search: str | None = None, status_in: list[str] | None = None,
    recorded_from: date | None = None, recorded_to: date | None = None,
    sort: str | None = None, dir: str = "asc", offset: int = 0, limit: int = 50,
    assessed_only: bool = False,
) -> tuple[list[Hazard], int]:
    """★ The register as a paged list (the list standard, 11-10). It was the
    latest 300 searched and paged in the browser: past 300 a hazard could not
    be found at all, and the search and dates only saw what had arrived."""
    from sqlalchemy import or_
    from app.crud._list_order import sort_clauses
    from app.services.day_range import day_bounds
    conds = []
    if status_in:
        conds.append(Hazard.status.in_(status_in))
    if assessed_only:
        conds.append(Hazard.risk_assessment_id.is_not(None))
    start, end = day_bounds(recorded_from, recorded_to)
    if start is not None:
        conds.append(Hazard.created_at >= start)
    if end is not None:
        conds.append(Hazard.created_at < end)
    if search and search.strip():
        # ★ Re-review N6: % and _ typed are looked for, not wildcards.
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{term}%"
        cols = (Hazard.hazard_no, Hazard.title, Hazard.description, Hazard.location_path,
                Hazard.department_name, Hazard.owner_name, Hazard.created_by_name,
                Hazard.current_rating, Hazard.hazard_type_label,
                func.cast(Hazard.hazard_types, String))
        conds.append(or_(*(c.ilike(like, escape="\\") for c in cols)))
    base = select(Hazard).where(*conds)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    ordering = sort_clauses(sort, dir, _hazard_sortable(),
                            tiebreak=[Hazard.created_at.desc(), Hazard.hazard_no.desc()]) \
        or [Hazard.created_at.desc(), Hazard.hazard_no.desc()]
    rows = list((await db.execute(base.order_by(*ordering).offset(offset).limit(limit))).scalars())
    return rows, total


async def get_hazard(db: AsyncSession, hazard_id: uuid.UUID) -> Hazard:
    hazard = await db.get(Hazard, hazard_id)
    if hazard is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hazard not found")
    return hazard


async def create_hazard(db: AsyncSession, payload: dict, *, creator_id: uuid.UUID) -> Hazard:
    payload = dict(payload)
    await _apply_lists(db, payload)
    path, dept_id, dept_name = await _place(db, payload.get("location_id"))
    owner_id = payload.get("owner_id") or creator_id
    from app.services import equipment_ref
    await equipment_ref.check(db, payload.get("equipment_id"))
    # The label follows the id and nothing else (crud.vocabulary.label_for).
    type_label = None
    if payload.get("hazard_type_id"):
        from app.crud.vocabulary import label_for
        type_label = await label_for(db, "hazard", payload["hazard_type_id"])
    hazard = Hazard(
        id=uuid.uuid4(),
        hazard_no=await next_number(db, "hazard"),
        title=payload["title"],
        description=payload.get("description"),
        # ★ Named here as well as on the model. This constructor copies fields
        # one by one, so a column added to the model and the schema — as
        # `findings` was in round 5 — is accepted by the API, stored by PATCH, and
        # silently dropped on create. Found driving the create form in a
        # browser; the server test had only exercised PATCH.
        findings=payload.get("findings"),
        hazard_type_id=payload.get("hazard_type_id"),
        hazard_type_label=type_label,
        hazard_types=payload.get("hazard_types") or (
            [{"id": str(payload["hazard_type_id"]), "label": type_label or ""}]
            if payload.get("hazard_type_id") else []),
        hazard_controls=payload.get("hazard_controls") or [],
        location_id=payload.get("location_id"),
        location_path=path, department_id=dept_id, department_name=dept_name,
        equipment_id=payload.get("equipment_id"),
        existing_controls=payload.get("existing_controls"),
        control_level=payload.get("control_level"),
        owner_id=owner_id,
        owner_name=await _name(db, owner_id),
        identified_on=payload.get("identified_on") or plant_today(),
        review_due_on=payload.get("review_due_on"),
        source_type=payload.get("source_type"),
        source_id=payload.get("source_id"),
        # Who recorded it, kept apart from the owner it defaults. Round 5's
        # monthly count is about who made the assessment.
        created_by=creator_id,
        created_by_name=await _name(db, creator_id),
    )
    db.add(hazard)
    await db.flush()
    return hazard


async def update_hazard(db: AsyncSession, hazard: Hazard, fields: dict, *,
                        editor_id: uuid.UUID | None = None) -> Hazard:
    fields = dict(fields)
    await _apply_lists(db, fields, hazard)
    if "location_id" in fields:
        (hazard.location_path, hazard.department_id,
         hazard.department_name) = await _place(db, fields["location_id"])
    if "owner_id" in fields:
        hazard.owner_name = await _name(db, fields["owner_id"])
    if "equipment_id" in fields:
        from app.services import equipment_ref
        await equipment_ref.check(db, fields["equipment_id"], hazard.equipment_id)
    if "hazard_type_id" in fields and fields["hazard_type_id"] != hazard.hazard_type_id:
        # A new kind takes the list's wording; a cleared one takes none.
        from app.crud.vocabulary import label_for
        fields["hazard_type_label"] = (
            await label_for(db, "hazard", fields["hazard_type_id"])
            if fields["hazard_type_id"] else None)
    else:
        # ★ The same kind keeps the wording it was recorded with (review,
        # uniops-8b): the edit form sends the id on every save, and an old
        # hazard edited for its description must not take today's wording.
        fields.pop("hazard_type_label", None)
    if "hazard_type_id" in fields and "hazard_types" not in fields:
        # An old client sending one kind: the list follows it.
        fields["hazard_types"] = ([{"id": str(fields["hazard_type_id"]),
                                    "label": fields.get("hazard_type_label")
                                    or hazard.hazard_type_label or ""}]
                                  if fields["hazard_type_id"] else [])
    changed = False
    for key, value in fields.items():
        if getattr(hazard, key) != value:
            changed = True
        setattr(hazard, key, value)
    if changed:
        # Kirk, round 12: "if the form is revised … last revised on".
        hazard.revised_at = datetime.now(timezone.utc)
        hazard.revised_by_name = await _name(db, editor_id)
    await db.flush()
    return hazard


async def attach_assessment(
    db: AsyncSession, hazard: Hazard, assessment: RiskAssessment, *, eliminated: bool = False,
) -> Hazard:
    """Make an assessment the hazard's current one.

    The ratings are copied onto the register line rather than read through the
    join every time. That is a denormalisation with a reason: the register's
    only use is as a list ranked by risk, and it would otherwise be a join and
    a sort over the whole table on every page load.
    """
    hazard.risk_assessment_id = assessment.id
    hazard.current_rating = assessment.rating
    hazard.residual_rating = assessment.residual_rating
    # ★ Round 12, HAZ-02: an assessment's `controls` is now what remains after
    # the controls, so it is no longer copied in as the controls in place.
    # The controls picked are, when the hazard has none of its own yet.
    if assessment.hazard_controls and not hazard.hazard_controls:
        hazard.hazard_controls = assessment.hazard_controls
    if eliminated:
        hazard.status = "eliminated"
    await db.flush()
    return hazard


async def hazard_summary(db: AsyncSession) -> list[dict]:
    """How many hazards sit at each rating, worst first."""
    rows = (await db.execute(
        select(Hazard.current_rating, func.count())
        .where(Hazard.status != "eliminated")
        .group_by(Hazard.current_rating)
    )).all()
    out = [{"rating": r or "Not assessed", "count": n} for r, n in rows]
    out.sort(key=lambda r: rating_rank(r["rating"]))
    return out


# ── Job safety analysis ──────────────────────────────────────────────────────

async def list_jsa(
    db: AsyncSession, *, status_in: list[str] | None = None, limit: int = 200
) -> list[Jsa]:
    stmt = select(Jsa)
    if status_in:
        stmt = stmt.where(Jsa.status.in_(status_in))
    return list((await db.execute(stmt.order_by(Jsa.title, Jsa.version.desc())
                                  .limit(limit))).scalars())


async def get_jsa(db: AsyncSession, jsa_id: uuid.UUID) -> Jsa:
    jsa = await db.get(Jsa, jsa_id)
    if jsa is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job safety analysis not found")
    return jsa


async def apply_jsa_lists(db: AsyncSession, fields: dict, *,
                          current_system: uuid.UUID | None = None,
                          current_ppe: list[dict] | None = None) -> None:
    """Round 12: PPE picked from the list, snapshotted; a System must be one
    still offered, unless it is the one the JSA already had."""
    ids = fields.pop("ppe_ids", None)
    if ids is not None:
        fields["ppe_items"] = await _picked(db, "ppe", ids, current_ppe)
    if fields.get("system_id") and fields["system_id"] != current_system:
        from app.models.vocabulary import VocabularyItem
        item = await db.get(VocabularyItem, fields["system_id"])
        if item is None or item.vocabulary_code != "equipment_system":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "No such System")
        if not item.is_active:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"The System '{item.label}' has been retired; pick another")


async def create_jsa(db: AsyncSession, payload: dict, *, creator_id: uuid.UUID) -> Jsa:
    from app.services import equipment_ref
    payload = dict(payload)
    await apply_jsa_lists(db, payload)
    await equipment_ref.check(db, payload.get("equipment_id"))
    path, dept_id, dept_name = await _place(db, payload.get("location_id"))
    jsa = Jsa(
        id=uuid.uuid4(),
        jsa_no=await next_number(db, "jsa"),
        title=payload["title"],
        job_description=payload.get("job_description"),
        location_id=payload.get("location_id"),
        location_path=path, department_id=dept_id, department_name=dept_name,
        equipment_id=payload.get("equipment_id"),
        required_ppe=payload.get("required_ppe"),
        # ★ Named here as well: this constructor copies fields one by one, and a
        # column left out is accepted by the API and silently dropped
        # (findings, round 5).
        ppe_items=payload.get("ppe_items") or [],
        system_id=payload.get("system_id"),
        review_due_on=payload.get("review_due_on"),
        created_by=creator_id,
        created_by_name=await _name(db, creator_id),
    )
    db.add(jsa)
    await db.flush()
    return jsa


async def steps_for(db: AsyncSession, jsa_id: uuid.UUID) -> list[JsaStep]:
    return list((await db.execute(
        select(JsaStep).where(JsaStep.jsa_id == jsa_id).order_by(JsaStep.seq))).scalars())


async def replace_steps(db: AsyncSession, jsa: Jsa, steps: list[dict]) -> list[JsaStep]:
    """Rewrite the step list wholesale.

    The editor is a table the author reorders and rewrites, so a diff would be
    more code and more ways to be wrong than replacing the rows. Refused once
    the analysis is active — a live method statement changes by superseding,
    not by editing under the crew who signed it.
    """
    if jsa.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{jsa.jsa_no} is {jsa.status}. Create a new version rather than editing "
            "one people have already signed.")

    # Picks the analysis already had keep their wording (B3/B4 review M2).
    had_hazards: list[dict] = []
    had_controls: list[dict] = []
    for existing in await steps_for(db, jsa.id):
        had_hazards += existing.hazard_items or []
        had_controls += existing.control_items or []
        await db.delete(existing)
    await db.flush()

    rows: list[JsaStep] = []
    for index, step in enumerate(steps, start=1):
        row = JsaStep(
            id=uuid.uuid4(), jsa_id=jsa.id, seq=index,
            step=step["step"],
            hazards=step.get("hazards"),
            controls=step.get("controls"),
            control_level=step.get("control_level"),
            hazard_id=step.get("hazard_id"),
            residual_rating=step.get("residual_rating"),
            hazard_items=await _picked(db, "hazard", step.get("hazard_ids") or [], had_hazards),
            control_items=await _picked(db, "hazard_control", step.get("control_ids") or [],
                                        had_controls),
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


async def activate(db: AsyncSession, jsa: Jsa, *, user_id: uuid.UUID) -> Jsa:
    """Approve it for use, and retire whatever it replaces.

    Refused with no steps, and refused when a step names a hazard and no
    control. That second check is the whole reason the analysis is a table:
    "we know this step is dangerous and have written nothing about what stops
    it" is exactly what must not be approved, and it is invisible in prose.
    """
    steps = await steps_for(db, jsa.id)
    if not steps:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Break the job into steps before approving it — an analysis with no "
            "steps is a title")

    # ★ Round 12, JSA-05: a hazard is named in words, picked from the list or
    # cited from the register, and a control in words or from the list.
    # Checking only the words would approve a step with three hazards picked
    # and nothing to stop them.
    uncontrolled = [s.seq for s in steps
                    if ((s.hazards or "").strip() or s.hazard_items or s.hazard_id)
                    and not ((s.controls or "").strip() or s.control_items)]
    if uncontrolled:
        listed = ", ".join(str(n) for n in uncontrolled)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Step{'s' if len(uncontrolled) > 1 else ''} {listed} "
            f"name{'' if len(uncontrolled) > 1 else 's'} a hazard with no control. "
            "Say what stops it, or say why nothing is needed.")

    jsa.status = "active"
    jsa.approved_at = datetime.now(timezone.utc)
    jsa.approved_by = user_id
    jsa.approved_by_name = await _name(db, user_id)

    if jsa.supersedes_id:
        previous = await db.get(Jsa, jsa.supersedes_id)
        if previous is not None and previous.status == "active":
            previous.status = "retired"

    await db.flush()
    return jsa


async def new_jsa_version(db: AsyncSession, jsa: Jsa, *, creator_id: uuid.UUID) -> Jsa:
    """A draft copy that will retire this one when approved.

    The steps come with it: a revision is almost always a change to two of
    fifteen steps, and starting from an empty table would guarantee the other
    thirteen get retyped and quietly differ.
    """
    draft = Jsa(
        id=uuid.uuid4(),
        jsa_no=await next_number(db, "jsa"),
        title=jsa.title, job_description=jsa.job_description,
        version=jsa.version + 1,
        location_id=jsa.location_id, location_path=jsa.location_path,
        department_id=jsa.department_id, department_name=jsa.department_name,
        equipment_id=jsa.equipment_id, required_ppe=jsa.required_ppe,
        ppe_items=list(jsa.ppe_items or []), system_id=jsa.system_id,
        review_due_on=jsa.review_due_on,
        supersedes_id=jsa.id,
        created_by=creator_id, created_by_name=await _name(db, creator_id),
    )
    db.add(draft)
    await db.flush()

    for step in await steps_for(db, jsa.id):
        db.add(JsaStep(
            id=uuid.uuid4(), jsa_id=draft.id, seq=step.seq, step=step.step,
            hazards=step.hazards, controls=step.controls,
            control_level=step.control_level, hazard_id=step.hazard_id,
            residual_rating=step.residual_rating,
            hazard_items=list(step.hazard_items or []),
            control_items=list(step.control_items or []),
        ))
    await db.flush()
    return draft


async def signoffs_for(
    db: AsyncSession, jsa_id: uuid.UUID, *, worked_on: date | None = None
) -> list[JsaSignoff]:
    stmt = select(JsaSignoff).where(JsaSignoff.jsa_id == jsa_id)
    if worked_on:
        stmt = stmt.where(JsaSignoff.worked_on == worked_on)
    return list((await db.execute(
        stmt.order_by(JsaSignoff.worked_on.desc(), JsaSignoff.signed_at))).scalars())


async def sign(
    db: AsyncSession, jsa: Jsa, people: list[dict], *, worked_on: date,
) -> list[JsaSignoff]:
    """The crew signs before starting. One device, passed round.

    Refused on a draft: signing an unapproved method statement records that a
    crew agreed to something nobody authorised.
    """
    if jsa.status != "active":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{jsa.jsa_no} is {jsa.status} and cannot be signed for work")

    already = {s.user_id for s in await signoffs_for(db, jsa.id, worked_on=worked_on)
               if s.user_id}
    added: list[JsaSignoff] = []
    for person in people:
        user_id = person.get("user_id")
        if user_id and uuid.UUID(str(user_id)) in already:
            continue
        row = JsaSignoff(
            id=uuid.uuid4(), jsa_id=jsa.id, version=jsa.version,
            user_id=uuid.UUID(str(user_id)) if user_id else None,
            person_name=person["person_name"],
            person_role=person.get("person_role"),
            signature=person.get("signature"),
            worked_on=worked_on,
            is_supervisor=bool(person.get("is_supervisor")),
        )
        db.add(row)
        added.append(row)
        if user_id:
            already.add(uuid.UUID(str(user_id)))
    await db.flush()
    return added
